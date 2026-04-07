"""Thin proactive runtime.

The reference tree keeps proactive ticking separate from prompt suggestion,
away-summary, and auto-dream lifecycles. This module therefore only manages
proactive state and tick cadence, while preserving a tiny compatibility
surface for callers that still set those callbacks.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from .core import _mutate_state, feature

TICK_TAG = "tick"
DEFAULT_TICK_INTERVAL_S = 10.0


class ProactiveStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass(slots=True)
class TickMetrics:
    tick_count: int = 0
    last_tick_ts: float = 0.0


@dataclass(slots=True)
class ProactiveState:
    active: bool = False
    paused: bool = False
    context_blocked: bool = False
    status: ProactiveStatus = ProactiveStatus.IDLE
    metrics: TickMetrics = field(default_factory=TickMetrics)


_state = ProactiveState()


def is_proactive_active() -> bool:
    return bool(_state.active) and feature("proactive")


def set_proactive_active(value: bool) -> None:
    _state.active = bool(value) and feature("proactive")
    _state.status = ProactiveStatus.RUNNING if _state.active else ProactiveStatus.STOPPED


def set_context_blocked(value: bool) -> None:
    _state.context_blocked = bool(value)
    _mutate_state(context_blocked=bool(value))


def is_context_blocked() -> bool:
    return _state.context_blocked


def pause_proactive() -> None:
    _state.paused = True
    _state.status = ProactiveStatus.PAUSED
    _mutate_state(paused=True)


def resume_proactive() -> None:
    _state.paused = False
    if _state.active and feature("proactive"):
        _state.status = ProactiveStatus.RUNNING
    _mutate_state(paused=False)


def stop_proactive() -> None:
    _state.active = False
    _state.paused = False
    _state.status = ProactiveStatus.STOPPED
    _mutate_state(paused=False)


def build_tick_message(*, local_time: str | None = None) -> dict[str, Any]:
    now = local_time or time.strftime("%Y-%m-%d %H:%M:%S %Z")
    _state.metrics.tick_count += 1
    _state.metrics.last_tick_ts = time.time()
    return {
        "role": "user",
        "content": f"<{TICK_TAG}>{now}</{TICK_TAG}>",
        "metadata": {
            "type": "tick",
            "tick_number": _state.metrics.tick_count,
            "timestamp": _state.metrics.last_tick_ts,
        },
    }


TickCallback = Callable[[dict[str, Any]], Awaitable[bool]]


async def run_proactive_loop(
    on_tick: TickCallback,
    *,
    interval_s: float = DEFAULT_TICK_INTERVAL_S,
    stop_event: asyncio.Event | None = None,
) -> None:
    if not feature("proactive"):
        if stop_event is not None:
            await stop_event.wait()
        return
    _state.active = True
    _state.status = ProactiveStatus.RUNNING
    stopper = stop_event or asyncio.Event()
    try:
        while not stopper.is_set():
            try:
                await asyncio.wait_for(stopper.wait(), timeout=interval_s)
                break
            except asyncio.TimeoutError:
                pass
            if _state.paused or _state.context_blocked:
                continue
            await on_tick(build_tick_message())
    finally:
        stop_proactive()


def get_proactive_system_prompt(*, brief_visibility: str = "") -> str | None:
    del brief_visibility
    if not feature("proactive") or not is_proactive_active():
        return None
    return (
        "Autonomous ticking is active. Treat <tick> prompts as timer wakes, "
        "and stay conservative unless there is clear queued work."
    )


def report_agent_action(did_sleep: bool) -> None:
    del did_sleep


class TickDebouncer:
    def __init__(self, min_interval_s: float = 1.0) -> None:
        self._min_interval_s = min_interval_s
        self._last_fire = 0.0

    def should_fire(self) -> bool:
        now = time.time()
        if now - self._last_fire < self._min_interval_s:
            return False
        self._last_fire = now
        return True


class IdleLevel(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"
    AWAY = "away"


class IdleDetector:
    def __init__(self, *, idle_after_s: float = 60.0, away_after_s: float = 300.0) -> None:
        self._idle_after_s = idle_after_s
        self._away_after_s = away_after_s
        self._last_user_input = time.time()

    def record_user_input(self) -> None:
        self._last_user_input = time.time()

    async def tick(self) -> IdleLevel:
        delta = time.time() - self._last_user_input
        if delta >= self._away_after_s:
            return IdleLevel.AWAY
        if delta >= self._idle_after_s:
            return IdleLevel.IDLE
        return IdleLevel.ACTIVE


_idle_detector = IdleDetector()


def get_idle_detector() -> IdleDetector:
    return _idle_detector


class ProactiveSuggestionEngine:
    """Compatibility holder for proactive-adjacent callbacks.

    Prompt suggestion, away-summary, and auto-dream are triggered by separate
    subsystems. ``evaluate()`` records the latest idle level but deliberately
    does not dispatch those callbacks.
    """

    def __init__(self) -> None:
        self._suggest_cb: Callable[[str, str], Awaitable[None]] | None = None
        self._away_summary_cb: Callable[[], Awaitable[None]] | None = None
        self._dream_trigger_cb: Callable[[], Awaitable[None]] | None = None
        self._last_level = IdleLevel.ACTIVE

    def set_suggest_callback(self, callback: Callable[[str, str], Awaitable[None]]) -> None:
        self._suggest_cb = callback

    def set_away_summary_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._away_summary_cb = callback

    def set_dream_trigger_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        self._dream_trigger_cb = callback

    async def evaluate(self, level: IdleLevel) -> None:
        self._last_level = level
        await asyncio.sleep(0)
