"""Sleep/Wake mechanism — lets the agent sleep and wake on triggers.

Port of Claude Code's SleepTool + message queue wake path. The agent
calls Sleep when there's nothing to do; it wakes when:
- A channel message arrives
- A cron task fires
- The user sends input
- The sleep duration expires
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Awaitable

from .core import _mutate_state, get_kairos_state

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command / message queue (mirrors messageQueueManager.ts)
# ---------------------------------------------------------------------------

@dataclass
class QueuedCommand:
    """An inbound message or command waiting to be processed."""
    source: str          # "channel", "cron", "user", "system"
    content: str
    priority: str = "normal"   # "normal", "next", "immediate"
    metadata: dict[str, Any] = field(default_factory=dict)
    enqueued_at: float = field(default_factory=time.time)


class CommandQueue:
    """Thread-safe async queue for inbound messages.

    SleepTool polls this to decide whether to wake early. Channel
    notifications, cron triggers, and user input all enqueue here.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[QueuedCommand] = asyncio.Queue()
        self._wake_event = asyncio.Event()

    async def enqueue(self, cmd: QueuedCommand) -> None:
        await self._queue.put(cmd)
        self._wake_event.set()

    def enqueue_nowait(self, cmd: QueuedCommand) -> None:
        self._queue.put_nowait(cmd)
        self._wake_event.set()

    async def dequeue(self, timeout: float | None = None) -> QueuedCommand | None:
        try:
            if timeout is not None and timeout <= 0:
                return self._queue.get_nowait()
            if timeout is not None:
                return await asyncio.wait_for(self._queue.get(), timeout=timeout)
            return await self._queue.get()
        except (asyncio.TimeoutError, asyncio.QueueEmpty):
            return None

    def has_commands(self) -> bool:
        return not self._queue.empty()

    @property
    def wake_event(self) -> asyncio.Event:
        return self._wake_event

    def clear_wake(self) -> None:
        self._wake_event.clear()

    def size(self) -> int:
        return self._queue.qsize()

    async def drain(self, max_items: int = 100) -> list[QueuedCommand]:
        items: list[QueuedCommand] = []
        while not self._queue.empty() and len(items) < max_items:
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return items


_command_queue: CommandQueue | None = None


def get_command_queue() -> CommandQueue:
    global _command_queue
    if _command_queue is None:
        _command_queue = CommandQueue()
    return _command_queue


# ---------------------------------------------------------------------------
# Wake reasons
# ---------------------------------------------------------------------------

class WakeReason(str, Enum):
    TIMEOUT = "timeout"           # Sleep duration expired
    CHANNEL_MESSAGE = "channel"   # Inbound channel notification
    CRON_TRIGGER = "cron"         # Scheduled task fired
    USER_INPUT = "user"           # User sent a message
    SYSTEM = "system"             # System event (shutdown, etc.)
    FORCED = "forced"             # Explicit wake call


@dataclass
class WakeResult:
    reason: WakeReason
    slept_for_s: float
    pending_commands: int
    wake_command: QueuedCommand | None = None


# ---------------------------------------------------------------------------
# Sleep implementation
# ---------------------------------------------------------------------------

DEFAULT_MIN_SLEEP_S = 5.0
DEFAULT_MAX_SLEEP_S = 300.0   # 5 minutes


async def sleep_until_wake(
    duration_s: float,
    *,
    queue: CommandQueue | None = None,
    poll_interval_s: float = 1.0,
    min_sleep_s: float = DEFAULT_MIN_SLEEP_S,
    max_sleep_s: float = DEFAULT_MAX_SLEEP_S,
    on_progress: Callable[[float, float], Awaitable[None]] | None = None,
) -> WakeResult:
    """Sleep for up to *duration_s*, waking early on queue activity.

    Parameters
    ----------
    duration_s:
        Requested sleep duration. Clamped to [min_sleep_s, max_sleep_s].
    queue:
        Command queue to poll. Uses the global queue if None.
    poll_interval_s:
        How often to check the queue (mirroring SleepTool's 1s poll).
    on_progress:
        Optional callback(elapsed, total) for progress reporting.

    Returns
    -------
    WakeResult with the reason for waking and how long we actually slept.
    """
    q = queue or get_command_queue()
    duration_s = max(min_sleep_s, min(duration_s, max_sleep_s))

    _mutate_state(sleeping=True)
    start = time.monotonic()
    elapsed = 0.0

    logger.debug("Sleep started (duration=%.1fs)", duration_s)

    try:
        while elapsed < duration_s:
            # Check for wake triggers BEFORE waiting (race-safe)
            if q.has_commands():
                cmd = await q.dequeue(timeout=0.0)
                if cmd is not None:
                    elapsed = time.monotonic() - start
                    reason = _command_to_wake_reason(cmd)
                    logger.debug(
                        "Sleep interrupted after %.1fs by %s",
                        elapsed, reason.value,
                    )
                    return WakeResult(
                        reason=reason,
                        slept_for_s=elapsed,
                        pending_commands=q.size(),
                        wake_command=cmd,
                    )

            remaining = duration_s - elapsed
            wait_time = min(poll_interval_s, remaining)

            # Clear then wait for the wake event
            q.clear_wake()
            try:
                await asyncio.wait_for(q.wake_event.wait(), timeout=wait_time)
            except asyncio.TimeoutError:
                pass

            elapsed = time.monotonic() - start

            # Progress callback
            if on_progress is not None:
                try:
                    await on_progress(elapsed, duration_s)
                except Exception:
                    pass

        # Normal timeout wake
        logger.debug("Sleep completed (%.1fs)", elapsed)
        return WakeResult(
            reason=WakeReason.TIMEOUT,
            slept_for_s=elapsed,
            pending_commands=q.size(),
        )

    finally:
        _mutate_state(sleeping=False, last_wake_ts=time.time())


def _command_to_wake_reason(cmd: QueuedCommand) -> WakeReason:
    mapping = {
        "channel": WakeReason.CHANNEL_MESSAGE,
        "cron": WakeReason.CRON_TRIGGER,
        "user": WakeReason.USER_INPUT,
        "system": WakeReason.SYSTEM,
    }
    return mapping.get(cmd.source, WakeReason.FORCED)


# ---------------------------------------------------------------------------
# Convenience: wake the agent from outside
# ---------------------------------------------------------------------------

async def wake_agent(
    reason: str = "system",
    content: str = "",
    **metadata: Any,
) -> None:
    """Push a wake command into the queue to interrupt Sleep."""
    q = get_command_queue()
    await q.enqueue(QueuedCommand(
        source=reason,
        content=content,
        priority="immediate",
        metadata=metadata,
    ))


def wake_agent_sync(
    reason: str = "system",
    content: str = "",
    **metadata: Any,
) -> None:
    """Non-async version for signal handlers / atexit."""
    q = get_command_queue()
    q.enqueue_nowait(QueuedCommand(
        source=reason,
        content=content,
        priority="immediate",
        metadata=metadata,
    ))


# ---------------------------------------------------------------------------
# Dual-track session persist (in-memory + disk)
# ---------------------------------------------------------------------------

import json
from pathlib import Path

from ..paths import mini_agent_path

_SLEEP_SESSION_PATH = mini_agent_path("sleep_session.json")


@dataclass
class SleepSessionState:
    """Snapshot of sleep/wake state for recovery."""
    sleeping: bool = False
    last_wake_ts: float = 0.0
    last_sleep_ts: float = 0.0
    total_sleep_s: float = 0.0
    wake_count: int = 0
    session_id: str = ""


_memory_session = SleepSessionState()


def persist_session_dual(
    state: SleepSessionState | None = None,
    path: Path | str | None = None,
) -> None:
    """Save session state to both in-memory cache and disk."""
    global _memory_session
    s = state or _memory_session
    _memory_session = s

    target = Path(path) if path else _SLEEP_SESSION_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "sleeping": s.sleeping,
        "last_wake_ts": s.last_wake_ts,
        "last_sleep_ts": s.last_sleep_ts,
        "total_sleep_s": s.total_sleep_s,
        "wake_count": s.wake_count,
        "session_id": s.session_id,
    }
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(target)


def restore_session(path: Path | str | None = None) -> SleepSessionState:
    """Restore session state — memory first, then disk fallback."""
    global _memory_session
    if _memory_session.session_id:
        return _memory_session

    target = Path(path) if path else _SLEEP_SESSION_PATH
    if not target.exists():
        return _memory_session

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        _memory_session = SleepSessionState(
            sleeping=data.get("sleeping", False),
            last_wake_ts=data.get("last_wake_ts", 0.0),
            last_sleep_ts=data.get("last_sleep_ts", 0.0),
            total_sleep_s=data.get("total_sleep_s", 0.0),
            wake_count=data.get("wake_count", 0),
            session_id=data.get("session_id", ""),
        )
    except (json.JSONDecodeError, OSError):
        pass
    return _memory_session


def get_sleep_session_state() -> SleepSessionState:
    return _memory_session


# ---------------------------------------------------------------------------
# Wake-on-event — typed wake triggers with callbacks
# ---------------------------------------------------------------------------

WakeCallback = Callable[[QueuedCommand], Awaitable[None]]


class WakeTriggerType(str, Enum):
    MESSAGE = "message"
    SCHEDULE = "schedule"
    CHANNEL = "channel"
    CUSTOM = "custom"


@dataclass
class WakeTrigger:
    trigger_type: WakeTriggerType
    callback: WakeCallback
    label: str = ""


class WakeOnEvent:
    """Register typed wake triggers beyond the default timer expiry."""

    def __init__(self) -> None:
        self._triggers: dict[WakeTriggerType, list[WakeTrigger]] = {
            t: [] for t in WakeTriggerType
        }

    def wake_on_message(self, callback: WakeCallback, label: str = "") -> None:
        self._triggers[WakeTriggerType.MESSAGE].append(
            WakeTrigger(WakeTriggerType.MESSAGE, callback, label)
        )

    def wake_on_schedule(self, callback: WakeCallback, label: str = "") -> None:
        self._triggers[WakeTriggerType.SCHEDULE].append(
            WakeTrigger(WakeTriggerType.SCHEDULE, callback, label)
        )

    def wake_on_channel(self, callback: WakeCallback, label: str = "") -> None:
        self._triggers[WakeTriggerType.CHANNEL].append(
            WakeTrigger(WakeTriggerType.CHANNEL, callback, label)
        )

    def register(self, trigger_type: WakeTriggerType, callback: WakeCallback, label: str = "") -> None:
        self._triggers[trigger_type].append(
            WakeTrigger(trigger_type, callback, label)
        )

    async def fire(self, trigger_type: WakeTriggerType, cmd: QueuedCommand) -> None:
        for trigger in self._triggers.get(trigger_type, []):
            try:
                await trigger.callback(cmd)
            except Exception:
                logger.exception("Wake trigger callback error (%s)", trigger.label)

    def clear(self, trigger_type: WakeTriggerType | None = None) -> None:
        if trigger_type:
            self._triggers[trigger_type].clear()
        else:
            for v in self._triggers.values():
                v.clear()


_wake_on_event: WakeOnEvent | None = None


def get_wake_on_event() -> WakeOnEvent:
    global _wake_on_event
    if _wake_on_event is None:
        _wake_on_event = WakeOnEvent()
    return _wake_on_event


# ---------------------------------------------------------------------------
# Sleep quality metrics
# ---------------------------------------------------------------------------

@dataclass
class SleepQualityMetrics:
    """Tracks quality-of-sleep indicators for monitoring."""

    sleep_duration_ms: float = 0.0
    interruption_count: int = 0
    deepest_sleep_level: int = 0  # 0=none, 1=light, 2=medium, 3=deep
    _sleep_start: float = 0.0

    def start_sleep(self) -> None:
        self._sleep_start = time.time()
        self.interruption_count = 0
        self.deepest_sleep_level = 0

    def record_interruption(self) -> None:
        self.interruption_count += 1

    def update_depth(self, elapsed_s: float, total_s: float) -> None:
        if total_s <= 0:
            return
        ratio = elapsed_s / total_s
        if ratio >= 0.6:
            self.deepest_sleep_level = max(self.deepest_sleep_level, 3)
        elif ratio >= 0.3:
            self.deepest_sleep_level = max(self.deepest_sleep_level, 2)
        elif ratio >= 0.1:
            self.deepest_sleep_level = max(self.deepest_sleep_level, 1)

    def end_sleep(self) -> None:
        if self._sleep_start > 0:
            self.sleep_duration_ms = (time.time() - self._sleep_start) * 1000.0
            self._sleep_start = 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "sleep_duration_ms": round(self.sleep_duration_ms, 1),
            "interruption_count": self.interruption_count,
            "deepest_sleep_level": self.deepest_sleep_level,
        }
