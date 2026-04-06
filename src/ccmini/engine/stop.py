"""Stop-condition checker for the query loop.

Replaces the naive ``if not tool_calls: return`` with richer heuristics
inspired by Claude Code's stop hooks:

- ``END_TURN`` — model finished naturally (no tool calls, or stop_reason)
- ``FORCE_STOP`` — consecutive empty replies, stuck loop, or budget exceeded
- ``CONTINUE`` — keep going (tool calls pending, progress being made)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..messages import ToolCallEvent, ToolResultBlock


class StopDecision(str, Enum):
    CONTINUE = "continue"
    END_TURN = "end_turn"
    FORCE_STOP = "force_stop"


@dataclass(slots=True)
class StopConfig:
    """Tuning knobs."""
    max_empty_replies: int = 2
    max_stable_rounds: int = 3


@dataclass
class StopChecker:
    """Stateful checker — accumulates round-by-round signals.

    Call ``record_round()`` after each provider response, then
    ``should_stop()`` to get the verdict.
    """

    config: StopConfig = field(default_factory=StopConfig)

    _consecutive_empty: int = field(default=0, init=False, repr=False)
    _consecutive_stable: int = field(default=0, init=False, repr=False)
    _last_tool_results_hash: int = field(default=0, init=False, repr=False)

    def record_round(
        self,
        *,
        reply_text: str,
        tool_calls: list[ToolCallEvent],
        tool_results: list[ToolResultBlock] | None = None,
    ) -> None:
        """Feed one round's output into the checker."""
        if not reply_text.strip() and not tool_calls:
            self._consecutive_empty += 1
        else:
            self._consecutive_empty = 0

        if tool_results is not None:
            h = _results_hash(tool_results)
            if h == self._last_tool_results_hash and h != 0:
                self._consecutive_stable += 1
            else:
                self._consecutive_stable = 0
            self._last_tool_results_hash = h

    def should_stop(self, *, has_tool_calls: bool) -> StopDecision:
        """Evaluate the stop condition after ``record_round``."""
        if not has_tool_calls:
            return StopDecision.END_TURN

        if self._consecutive_empty >= self.config.max_empty_replies:
            return StopDecision.FORCE_STOP

        if self._consecutive_stable >= self.config.max_stable_rounds:
            return StopDecision.FORCE_STOP

        return StopDecision.CONTINUE

    def reset(self) -> None:
        self._consecutive_empty = 0
        self._consecutive_stable = 0
        self._last_tool_results_hash = 0


def _results_hash(results: list[ToolResultBlock]) -> int:
    """Cheap hash of tool result contents to detect "stuck" loops."""
    parts: list[str] = []
    for r in results:
        parts.append(f"{r.tool_use_id}:{r.content[:200]}:{r.is_error}")
    return hash(tuple(parts))


# ── Graceful shutdown hooks ──────────────────────────────────────────

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

ShutdownCallback = Callable[[], Awaitable[None] | None]


class StopHookChain:
    """Registry of callbacks to execute when the agent stops.

    Hooks are invoked in registration order.  Each hook receives no
    arguments and may be sync or async.  Errors in one hook do not
    prevent subsequent hooks from running.
    """

    def __init__(self) -> None:
        self._hooks: list[tuple[str, ShutdownCallback]] = []

    def register(self, callback: ShutdownCallback, *, name: str = "") -> None:
        """Register a shutdown hook.  *name* is used in log messages."""
        label = name or getattr(callback, "__name__", repr(callback))
        self._hooks.append((label, callback))

    def unregister(self, callback: ShutdownCallback) -> None:
        """Remove a previously registered hook."""
        self._hooks = [(n, cb) for n, cb in self._hooks if cb is not callback]

    @property
    def count(self) -> int:
        return len(self._hooks)

    async def run_all(self) -> list[str]:
        """Run every registered hook, returning a list of error messages (if any)."""
        errors: list[str] = []
        for name, hook in self._hooks:
            try:
                result = hook()
                if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                    await result
            except Exception as exc:
                msg = f"Shutdown hook '{name}' failed: {exc}"
                logger.warning(msg)
                errors.append(msg)
        return errors

    def clear(self) -> None:
        self._hooks.clear()


def register_shutdown_hook(
    chain: StopHookChain,
    callback: ShutdownCallback,
    *,
    name: str = "",
) -> None:
    """Convenience function to register a hook on *chain*."""
    chain.register(callback, name=name)


# ── Stop reason tracking ────────────────────────────────────────────


class StopReason(str, Enum):
    """Fine-grained reason why the query loop stopped.

    ``StopDecision`` gives the broad verdict (continue / end / force);
    ``StopReason`` records *why* a particular decision was reached.
    """
    MAX_TURNS = "max_turns"
    FORCE_STOP = "force_stop"
    USER_CANCEL = "user_cancel"
    ERROR = "error"
    NATURAL_END = "natural_end"
    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"
    IDLE_DETECTED = "idle_detected"
    HOOK_STOPPED = "hook_stopped"


class StopReasonTracker:
    """Track the specific reason the query loop terminated.

    Attached to ``StopChecker`` so callers can query it after
    ``should_stop()`` returns a non-CONTINUE decision.
    """

    def __init__(self) -> None:
        self._reason: StopReason | None = None
        self._detail: str = ""

    def set(self, reason: StopReason, detail: str = "") -> None:
        self._reason = reason
        self._detail = detail

    @property
    def reason(self) -> StopReason | None:
        return self._reason

    @property
    def detail(self) -> str:
        return self._detail

    def reset(self) -> None:
        self._reason = None
        self._detail = ""

    def __repr__(self) -> str:
        return f"StopReasonTracker(reason={self._reason}, detail={self._detail!r})"


# ── Idle detection ──────────────────────────────────────────────────


@dataclass(slots=True)
class IdleConfig:
    """Configuration for idle/repetitive output detection."""
    min_rounds: int = 3
    similarity_threshold: float = 0.85
    max_empty_streak: int = 5


def detect_idle_loop(
    recent_texts: list[str],
    config: IdleConfig = IdleConfig(),
) -> bool:
    """Check whether the model is producing repetitive or empty output.

    Returns ``True`` if the last *min_rounds* outputs are suspiciously
    similar or all empty, suggesting the model is stuck in a loop.
    """
    if len(recent_texts) < config.min_rounds:
        return False

    window = recent_texts[-config.min_rounds:]

    if all(not t.strip() for t in window):
        return True

    non_empty = [t.strip() for t in window if t.strip()]
    if len(non_empty) < 2:
        return False

    reference = non_empty[-1]
    similar_count = 0
    for text in non_empty[:-1]:
        if _text_similarity(text, reference) >= config.similarity_threshold:
            similar_count += 1

    return similar_count >= config.min_rounds - 1


def _text_similarity(a: str, b: str) -> float:
    """Quick similarity ratio between two strings.

    Uses a length-based heuristic with character-set overlap — much
    cheaper than full edit-distance but good enough for loop detection.
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0

    len_ratio = min(len(a), len(b)) / max(len(a), len(b))

    # Trigram overlap for content similarity
    def trigrams(s: str) -> set[str]:
        return {s[i:i+3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}

    ta, tb = trigrams(a[:500]), trigrams(b[:500])
    if not ta or not tb:
        return len_ratio
    overlap = len(ta & tb) / max(len(ta | tb), 1)

    return (len_ratio + overlap) / 2.0


class EnhancedStopChecker(StopChecker):
    """Extended ``StopChecker`` with stop-reason tracking and idle detection.

    Drop-in replacement for ``StopChecker`` that additionally records
    *why* the loop stopped and can detect repetitive outputs.
    """

    def __init__(
        self,
        config: StopConfig | None = None,
        idle_config: IdleConfig | None = None,
    ) -> None:
        super().__init__(config=config or StopConfig())
        self.reason_tracker = StopReasonTracker()
        self._idle_config = idle_config or IdleConfig()
        self._recent_texts: list[str] = []
        self._hook_chain = StopHookChain()

    @property
    def hook_chain(self) -> StopHookChain:
        return self._hook_chain

    def record_round(
        self,
        *,
        reply_text: str,
        tool_calls: list[ToolCallEvent],
        tool_results: list[ToolResultBlock] | None = None,
    ) -> None:
        super().record_round(
            reply_text=reply_text,
            tool_calls=tool_calls,
            tool_results=tool_results,
        )
        # Tool-only turns are expected during multi-step execution and
        # should not be treated as idle conversational output.
        if reply_text.strip() or not tool_calls:
            self._recent_texts.append(reply_text)
            if len(self._recent_texts) > 20:
                self._recent_texts = self._recent_texts[-20:]

    def should_stop(self, *, has_tool_calls: bool) -> StopDecision:
        decision = super().should_stop(has_tool_calls=has_tool_calls)

        if decision == StopDecision.END_TURN:
            self.reason_tracker.set(StopReason.NATURAL_END)
        elif decision == StopDecision.FORCE_STOP:
            if self._consecutive_empty >= self.config.max_empty_replies:
                self.reason_tracker.set(
                    StopReason.FORCE_STOP,
                    f"consecutive empty replies: {self._consecutive_empty}",
                )
            elif self._consecutive_stable >= self.config.max_stable_rounds:
                self.reason_tracker.set(
                    StopReason.FORCE_STOP,
                    f"stable tool results: {self._consecutive_stable}",
                )

        if decision == StopDecision.CONTINUE and detect_idle_loop(
            self._recent_texts, self._idle_config
        ):
            self.reason_tracker.set(
                StopReason.IDLE_DETECTED,
                "repetitive output detected",
            )
            return StopDecision.FORCE_STOP

        return decision

    def get_stop_reason(self) -> StopReason | None:
        return self.reason_tracker.reason

    def reset(self) -> None:
        super().reset()
        self.reason_tracker.reset()
        self._recent_texts.clear()


# ── Circuit breaker for stop-related failures ────────────────────────


@dataclass(slots=True)
class StopCircuitBreaker:
    """Prevent repeated stop/restart thrashing.

    When the agent hits the same stop condition multiple times in a row
    (e.g. repeated ``ContextTooLongError`` → compact → overflow again),
    the circuit breaker trips and forces a permanent stop rather than
    looping forever.

    Ported from Claude Code's ``MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES``
    pattern in ``autoCompact.ts``.
    """
    max_consecutive: int = 3
    _consecutive: int = 0
    _last_reason: str = ""

    @property
    def is_tripped(self) -> bool:
        return self._consecutive >= self.max_consecutive

    def record(self, reason: str) -> bool:
        """Record a failure.  Returns ``True`` if the breaker just tripped."""
        if reason == self._last_reason:
            self._consecutive += 1
        else:
            self._consecutive = 1
            self._last_reason = reason
        if self.is_tripped:
            logger.warning(
                "Stop circuit breaker tripped: '%s' occurred %d consecutive times",
                reason, self._consecutive,
            )
            return True
        return False

    def record_success(self) -> None:
        self._consecutive = 0
        self._last_reason = ""

    def reset(self) -> None:
        self._consecutive = 0
        self._last_reason = ""


# ── Max-output-tokens escalation tracker ─────────────────────────────


@dataclass(slots=True)
class OutputTokensEscalation:
    """Track max_output_tokens escalation after overflow errors.

    Mirrors TS ``retryContext.maxTokensOverride``: on first overflow,
    escalate to ESCALATED_MAX_TOKENS (64k). That's it — TS does NOT
    do multi-level escalation.
    """
    escalated_tokens: int = 64_000
    current_override: int | None = None
    _escalated: bool = False

    def escalate(self, target: int = 0) -> int:
        """Compute the next max_output_tokens after an overflow.

        Returns the escalated value. TS only escalates once.
        """
        value = target if target > 0 else self.escalated_tokens
        self.current_override = value
        self._escalated = True
        return value

    def reset(self) -> None:
        self.current_override = None
        self._escalated = False


# ── Persistent retry awareness ───────────────────────────────────────


@dataclass(slots=True)
class PersistentRetryState:
    """State for unattended sessions that retry 429/529 indefinitely.

    Ported from Claude Code's ``CLAUDE_CODE_UNATTENDED_RETRY`` pattern
    in ``withRetry.ts``.  These sessions run in CI/background and should
    never give up on transient capacity errors — they back off with
    exponential delay and emit heartbeats so the host doesn't time out.
    """
    enabled: bool = False
    attempt: int = 0
    max_backoff_seconds: float = 300.0
    reset_cap_seconds: float = 21_600.0
    heartbeat_interval_seconds: float = 30.0

    def get_delay(self) -> float:
        import random
        self.attempt += 1
        base = min(1.0 * (2 ** self.attempt), self.max_backoff_seconds)
        jitter = random.uniform(0, base * 0.2)
        return min(base + jitter, self.reset_cap_seconds)

    def reset(self) -> None:
        self.attempt = 0


# ── Consecutive failure tracker ──────────────────────────────────────


@dataclass
class ConsecutiveFailureTracker:
    """Generic tracker for any repeated-failure pattern.

    Used by the query loop to detect when the same error keeps
    recurring across turns, signalling a systemic problem (bad auth,
    irrecoverable context size, etc.) rather than a transient blip.
    """
    max_failures: int = 5
    _counts: dict[str, int] = field(default_factory=dict)

    def record(self, error_type: str) -> bool:
        """Record a failure.  Returns ``True`` if the threshold is reached."""
        self._counts[error_type] = self._counts.get(error_type, 0) + 1
        return self._counts[error_type] >= self.max_failures

    def clear(self, error_type: str | None = None) -> None:
        if error_type is None:
            self._counts.clear()
        else:
            self._counts.pop(error_type, None)

    def count(self, error_type: str) -> int:
        return self._counts.get(error_type, 0)

    def summary(self) -> dict[str, int]:
        return dict(self._counts)
