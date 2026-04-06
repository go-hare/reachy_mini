"""Retry / exponential backoff for LLM provider calls.

Ported from Claude Code's ``withRetry.ts`` — a sophisticated retry layer
handling transient errors, rate limits, model fallback, and context
overflow recovery.

Key features beyond basic exponential backoff:
- Retry-After header support
- Consecutive 529 tracking -> FallbackTriggeredError
- max_tokens context overflow auto-adjustment
- Distinct foreground/background retry policies
- Persistent retry mode for long-running unattended tasks
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({
    401,  # auth refresh / transient auth
    408,  # request timeout
    409,  # lock timeout
    429,  # rate limit
    500,  # internal server error
    502,  # bad gateway
    503,  # service unavailable
    529,  # overloaded (Anthropic)
})

BASE_DELAY_MS = 500
DEFAULT_MAX_RETRIES = 10
MAX_529_RETRIES = 3
FLOOR_OUTPUT_TOKENS = 3000
PERSISTENT_MAX_BACKOFF_MS = 5 * 60.0
PERSISTENT_RESET_CAP_MS = 6 * 60 * 60.0


class QuerySource(str, Enum):
    """Classification of where a query originates — affects retry behaviour."""
    MAIN = "main"
    AGENT = "agent"
    COMPACT = "compact"
    SIDE_QUERY = "side_query"
    AUTO_MODE = "auto_mode"
    SESSION_MEMORY = "session_memory"
    BACKGROUND = "background"


FOREGROUND_SOURCES = frozenset({
    QuerySource.MAIN,
    QuerySource.AGENT,
    QuerySource.COMPACT,
    QuerySource.AUTO_MODE,
})


@dataclass(frozen=True, slots=True)
class RetryConfig:
    """Tuning knobs for the retry wrapper."""

    max_retries: int = DEFAULT_MAX_RETRIES
    base_delay: float = BASE_DELAY_MS / 1000
    max_delay: float = 32.0
    jitter: bool = True
    retryable_status_codes: frozenset[int] = RETRYABLE_STATUS_CODES
    max_529_before_fallback: int = MAX_529_RETRIES
    overloaded_multiplier: float = 1.0
    persistent_retry: bool = False
    persistent_max_backoff: float = 300.0  # 5 minutes
    persistent_reset_cap: float = 21600.0  # 6 hours


DEFAULT_RETRY = RetryConfig()


# ── Error types ─────────────────────────────────────────────────────

class RetryableError(Exception):
    """Wrapper carrying HTTP status and optional Retry-After."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        retry_after: float | None = None,
        original: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.original = original


class ContextTooLongError(Exception):
    """Prompt exceeds context window — react by compacting, not retrying."""


class CannotRetryError(Exception):
    """All retries exhausted or non-retryable error."""

    def __init__(self, original: BaseException, *, max_tokens_override: int | None = None) -> None:
        super().__init__(str(original))
        self.original = original
        self.max_tokens_override = max_tokens_override


class FallbackTriggeredError(Exception):
    """Consecutive overloads triggered model fallback."""

    def __init__(self, original_model: str, fallback_model: str) -> None:
        super().__init__(f"Model fallback: {original_model} -> {fallback_model}")
        self.original_model = original_model
        self.fallback_model = fallback_model


# ── Retry context (mutable state within a retry loop) ───────────────

@dataclass
class RetryContext:
    """Mutable state carried through a retry loop."""

    model: str = ""
    max_tokens_override: int | None = None
    consecutive_529: int = 0

    def record_529(self) -> None:
        self.consecutive_529 += 1

    def reset_529(self) -> None:
        self.consecutive_529 = 0


# ── Helper functions ────────────────────────────────────────────────

def _extract_status_code(exc: BaseException) -> int:
    """Best-effort extraction of HTTP status from SDK exceptions."""
    for attr in ("status_code", "status", "code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    response = getattr(exc, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None) or getattr(response, "status", None)
        if isinstance(code, int):
            return code
    return 0


def _extract_retry_after(exc: BaseException) -> float | None:
    """Try to read Retry-After header from the exception/response."""
    response = getattr(exc, "response", None)
    if response is None:
        headers = getattr(exc, "headers", None)
        if headers is not None:
            raw = None
            if hasattr(headers, "get"):
                raw = headers.get("retry-after") or headers.get("Retry-After")
            if raw:
                try:
                    return float(raw)
                except (ValueError, TypeError):
                    return None
        return None
    headers = getattr(response, "headers", None) or {}
    if hasattr(headers, "get"):
        raw = headers.get("retry-after") or headers.get("Retry-After")
    else:
        raw = headers.get("retry-after") if isinstance(headers, dict) else None
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _is_context_too_long(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "prompt is too long" in msg
        or "prompt_too_long" in msg
        or "context_length_exceeded" in msg
    )


def _is_overloaded(exc: BaseException) -> bool:
    status = _extract_status_code(exc)
    if status == 529:
        return True
    msg = str(exc).lower()
    return "overloaded" in msg or '"type":"overloaded_error"' in msg


def _is_oauth_token_revoked(exc: BaseException) -> bool:
    return (
        _extract_status_code(exc) == 403
        and "oauth token has been revoked" in str(exc).lower()
    )


def _is_transient_capacity_error(exc: BaseException) -> bool:
    status = _extract_status_code(exc)
    return _is_overloaded(exc) or status == 429


def _should_retry_529(query_source: QuerySource | str | None) -> bool:
    return query_source is None or query_source in FOREGROUND_SOURCES


def get_default_max_retries() -> int:
    raw = os.environ.get("CLAUDE_CODE_MAX_RETRIES", "").strip()
    if raw.isdigit():
        return int(raw)
    return DEFAULT_MAX_RETRIES


def _is_persistent_retry_enabled() -> bool:
    return os.environ.get("CLAUDE_CODE_UNATTENDED_RETRY", "").strip().lower() in {"1", "true", "yes", "on"}


def _get_rate_limit_reset_delay(exc: BaseException) -> float | None:
    headers = getattr(exc, "headers", None)
    raw = None
    if headers is not None and hasattr(headers, "get"):
        raw = headers.get("anthropic-ratelimit-unified-reset")
    response = getattr(exc, "response", None)
    if raw is None and response is not None:
        resp_headers = getattr(response, "headers", None)
        if resp_headers is not None and hasattr(resp_headers, "get"):
            raw = resp_headers.get("anthropic-ratelimit-unified-reset")
    if raw is None:
        return None
    try:
        reset_unix = float(raw)
    except (TypeError, ValueError):
        return None
    delay = reset_unix - time.time()
    return delay if delay > 0 else None


def _parse_max_tokens_overflow(exc: BaseException) -> tuple[int, int, int] | None:
    """Parse 'input + max_tokens > context limit: X + Y > Z' errors.

    Returns (input_tokens, max_tokens, context_limit) or None.
    """
    import re
    msg = str(exc)
    if "max_tokens" not in msg.lower() or "context" not in msg.lower():
        return None
    match = re.search(r"(\d+)\s*\+\s*(\d+)\s*>\s*(\d+)", msg)
    if not match:
        return None
    try:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))
    except (ValueError, IndexError):
        return None


def get_retry_delay(
    attempt: int,
    retry_after: float | None = None,
    *,
    max_delay: float = 32.0,
    base_delay: float = BASE_DELAY_MS / 1000,
) -> float:
    """Compute delay for an attempt with exponential backoff + jitter."""
    if retry_after is not None and retry_after > 0:
        return retry_after

    base = min(base_delay * (2 ** attempt), max_delay)
    jitter = random.random() * 0.25 * base
    return base + jitter


def _should_retry(exc: BaseException, config: RetryConfig) -> bool:
    """Decide whether an error is retryable — aligned with TS shouldRetry()."""
    if _is_context_too_long(exc):
        return False

    # Persistent mode: 429/529 always retryable
    if _is_persistent_retry_enabled() and _is_transient_capacity_error(exc):
        return True

    # Overloaded error by message content (SDK sometimes loses 529 status)
    msg_str = str(exc).lower()
    if '"type":"overloaded_error"' in str(exc):
        return True

    # Max tokens context overflow — retryable (handled by caller)
    if _parse_max_tokens_overflow(exc):
        return True

    # x-should-retry header (TS line 732-751)
    headers = getattr(exc, "headers", None)
    if headers is not None and hasattr(headers, "get"):
        should_retry_header = headers.get("x-should-retry")
        if should_retry_header == "true":
            return True
        if should_retry_header == "false":
            status = _extract_status_code(exc)
            is_5xx = status is not None and status >= 500
            if not is_5xx:
                return False

    status = _extract_status_code(exc)

    # Connection errors are retryable
    exc_name = type(exc).__name__.lower()
    if any(k in exc_name for k in ("connection", "timeout", "reset")):
        return True

    if not status:
        return False

    # 408 request timeout, 409 lock timeout
    if status in (408, 409):
        return True

    # 429 rate limit
    if status == 429:
        return True

    # 401 auth error — retryable
    if status == 401:
        return True

    # 403 OAuth token revoked
    if _is_oauth_token_revoked(exc):
        return True

    # 5xx server errors
    if status >= 500:
        return True

    # 529 overloaded
    if status == 529:
        return True

    return False


# ── Main retry wrapper ──────────────────────────────────────────────

async def with_retry(
    fn: Callable[[], Coroutine[Any, Any, T]],
    config: RetryConfig = DEFAULT_RETRY,
    *,
    query_source: QuerySource = QuerySource.MAIN,
    fallback_model: str = "",
    model: str = "",
    on_retry: Callable[[int, float, BaseException], Coroutine[Any, Any, None]] | None = None,
) -> T:
    """Call *fn* with exponential backoff, fallback detection, and overflow recovery.

    Key behaviours (matching Claude Code):
    - Consecutive 529 → FallbackTriggeredError after MAX_529_RETRIES
    - 400 max_tokens overflow → auto-adjust and retry
    - Background queries bail on 529 (no retry amplification)
    - ContextTooLongError raised immediately (no retry)
    """
    ctx = RetryContext(model=model)
    last_exc: BaseException | None = None

    effective_max = config.max_retries if config.max_retries != DEFAULT_MAX_RETRIES else get_default_max_retries()
    persistent_attempt = 0

    for attempt in range(effective_max + 1):
        try:
            result = await fn()
            ctx.reset_529()
            return result

        except Exception as exc:
            last_exc = exc

            # Context too long — never retry
            if _is_context_too_long(exc):
                raise ContextTooLongError(str(exc)) from exc

            status = _extract_status_code(exc)
            persistent = config.persistent_retry or _is_persistent_retry_enabled()

            # Background sources bail on 529 immediately
            if _is_overloaded(exc) and not _should_retry_529(query_source):
                raise CannotRetryError(exc) from exc

            # Track consecutive 529 for fallback
            if _is_overloaded(exc):
                ctx.record_529()
                if ctx.consecutive_529 >= config.max_529_before_fallback and fallback_model:
                    raise FallbackTriggeredError(model, fallback_model) from exc
            else:
                ctx.reset_529()

            # max_tokens overflow → adjust and retry
            overflow = _parse_max_tokens_overflow(exc)
            if overflow:
                input_tokens, _, context_limit = overflow
                available = max(0, context_limit - input_tokens - 1000)
                if available >= FLOOR_OUTPUT_TOKENS:
                    ctx.max_tokens_override = max(FLOOR_OUTPUT_TOKENS, available)
                    logger.info(
                        "max_tokens overflow: adjusted to %d", ctx.max_tokens_override,
                    )
                    continue

            # Not retryable → raise
            if not _should_retry(exc, config):
                if attempt >= effective_max:
                    raise CannotRetryError(exc, max_tokens_override=ctx.max_tokens_override) from exc
                raise

            if attempt >= effective_max:
                if persistent and _is_transient_capacity_error(exc):
                    persistent_attempt += 1
                    retry_after = _get_rate_limit_reset_delay(exc) or _extract_retry_after(exc)
                    delay = get_retry_delay(
                        persistent_attempt,
                        retry_after,
                        max_delay=min(config.persistent_max_backoff, PERSISTENT_MAX_BACKOFF_MS),
                    )
                    delay = min(delay, config.persistent_reset_cap, PERSISTENT_RESET_CAP_MS)
                    logger.info(
                        "Persistent retry %d after %.1fs", persistent_attempt, delay,
                    )
                    if on_retry:
                        await on_retry(persistent_attempt, delay, exc)
                    await asyncio.sleep(delay)
                    continue
                raise CannotRetryError(exc, max_tokens_override=ctx.max_tokens_override) from exc

            retry_after = _extract_retry_after(exc)
            is_overloaded = _is_overloaded(exc)
            delay = get_retry_delay(
                attempt, retry_after,
                max_delay=config.max_delay,
                base_delay=config.base_delay,
            )

            logger.warning(
                "Retry %d/%d after %.1fs (status=%d): %s",
                attempt + 1, config.max_retries, delay, status, exc,
            )
            if on_retry:
                await on_retry(attempt + 1, delay, exc)
            await asyncio.sleep(delay)

    assert last_exc is not None
    raise CannotRetryError(last_exc)


async def with_retry_stream(
    fn: Callable[[], Coroutine[Any, Any, AsyncIterator[T]]],
    config: RetryConfig = DEFAULT_RETRY,
    *,
    query_source: QuerySource = QuerySource.MAIN,
    fallback_model: str = "",
    model: str = "",
) -> AsyncIterator[T]:
    """Like ``with_retry`` but for async-iterator-returning callables.

    Retries only the *connection* phase. Once streaming begins, errors
    propagate (partial streams can't be retried safely).
    """
    ctx = RetryContext(model=model)
    last_exc: BaseException | None = None

    effective_max = config.max_retries if config.max_retries != DEFAULT_MAX_RETRIES else get_default_max_retries()
    persistent_attempt = 0

    for attempt in range(effective_max + 1):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc

            if _is_context_too_long(exc):
                raise ContextTooLongError(str(exc)) from exc

            persistent = config.persistent_retry or _is_persistent_retry_enabled()

            if _is_overloaded(exc) and not _should_retry_529(query_source):
                raise CannotRetryError(exc) from exc

            if _is_overloaded(exc):
                ctx.record_529()
                if ctx.consecutive_529 >= config.max_529_before_fallback and fallback_model:
                    raise FallbackTriggeredError(model, fallback_model) from exc
            else:
                ctx.reset_529()

            if not _should_retry(exc, config):
                raise

            if attempt >= effective_max:
                if persistent and _is_transient_capacity_error(exc):
                    persistent_attempt += 1
                    retry_after = _get_rate_limit_reset_delay(exc) or _extract_retry_after(exc)
                    delay = get_retry_delay(
                        persistent_attempt,
                        retry_after,
                        max_delay=min(config.persistent_max_backoff, PERSISTENT_MAX_BACKOFF_MS),
                    )
                    delay = min(delay, config.persistent_reset_cap, PERSISTENT_RESET_CAP_MS)
                    logger.warning(
                        "Persistent stream retry %d after %.1fs (status=%d): %s",
                        persistent_attempt, delay, _extract_status_code(exc), exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise CannotRetryError(exc) from exc

            retry_after = _extract_retry_after(exc)
            delay = get_retry_delay(attempt, retry_after, max_delay=config.max_delay)
            if _is_overloaded(exc):
                delay *= config.overloaded_multiplier

            logger.warning(
                "Stream retry %d/%d after %.1fs (status=%d): %s",
                attempt + 1, config.max_retries, delay, _extract_status_code(exc), exc,
            )
            await asyncio.sleep(delay)

    assert last_exc is not None
    raise last_exc
