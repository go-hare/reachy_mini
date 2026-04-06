"""Away Summary — "while you were away" session recap.

Ported from Claude Code's ``awaySummary.ts``:
- Generates a short (1-3 sentence) recap when the user returns
- Uses session memory for broader context if available
- Truncates to recent 30 messages to stay within prompt limits
- Returns None on abort, empty transcript, or error
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from ..messages import Message, user_message

if TYPE_CHECKING:
    from ..providers import BaseProvider

logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────

RECENT_MESSAGE_WINDOW = 30

AWAY_SUMMARY_SYSTEM = """\
You generate brief session recaps for users returning to a coding session. \
Write exactly 1-3 short sentences. Be concrete and specific."""


def _build_prompt(session_memory: str | None = None) -> str:
    """Build the away summary prompt."""
    memory_block = ""
    if session_memory:
        memory_block = f"Session memory (broader context):\n{session_memory}\n\n"

    return (
        f"{memory_block}"
        "The user stepped away and is coming back. Write exactly 1-3 short "
        "sentences. Start by stating the high-level task — what they are "
        "building or debugging, not implementation details. Next: the "
        "concrete next step. Skip status reports and commit recaps."
    )


# ── Generation ──────────────────────────────────────────────────────

async def generate_away_summary(
    messages: list[Message],
    provider: BaseProvider,
    *,
    session_memory: str | None = None,
) -> str | None:
    """Generate a session recap for the "while you were away" card.

    Returns the summary string or None on failure/empty transcript.
    """
    if not messages:
        return None

    from ..delegation.fork import run_forked_side_query

    recent = messages[-RECENT_MESSAGE_WINDOW:]

    # Build conversation context
    conv_parts: list[str] = []
    for msg in recent:
        text = msg.text.strip()[:2000]
        if text:
            conv_parts.append(f"[{msg.role.upper()}]: {text}")
    conversation = "\n\n".join(conv_parts)

    prompt = _build_prompt(session_memory)
    full_prompt = f"{conversation}\n\n---\n\n{prompt}"

    try:
        result = await run_forked_side_query(
            provider=provider,
            parent_messages=recent,
            system_prompt=AWAY_SUMMARY_SYSTEM,
            prompt=full_prompt,
            max_tokens=256,
            temperature=0.0,
            query_source="away_summary",
        )

        summary = result.strip()
        if summary:
            logger.debug("Away summary: %s", summary)
            return summary
        return None

    except Exception as exc:
        logger.debug("Away summary generation failed: %s", exc)
        return None


# ── Idle detection ──────────────────────────────────────────────────


def should_show_summary(
    last_activity_time: float,
    *,
    idle_threshold_seconds: float = 300.0,
) -> bool:
    """Check if enough time has passed to show a "you were away" summary.

    Default threshold: 5 minutes of inactivity.
    """
    if last_activity_time <= 0:
        return False
    return (time.time() - last_activity_time) >= idle_threshold_seconds


class AwaySummaryManager:
    """Manages away summary generation and display state.

    Usage:
        manager = AwaySummaryManager(provider)
        manager.mark_activity()  # call on each user interaction

        # When user returns after idle:
        if manager.should_show():
            summary = await manager.generate(messages)
    """

    def __init__(
        self,
        provider: BaseProvider,
        *,
        idle_threshold: float = 300.0,
    ) -> None:
        self._provider = provider
        self._idle_threshold = idle_threshold
        self._last_activity: float = time.time()
        self._last_summary: str | None = None
        self._summary_shown: bool = False

    def mark_activity(self) -> None:
        """Record user activity (call on each interaction)."""
        self._last_activity = time.time()
        self._summary_shown = False

    def should_show(self) -> bool:
        """Check if an away summary should be displayed."""
        if self._summary_shown:
            return False
        if self._last_activity <= 0:
            return False
        elapsed = time.time() - self._last_activity
        return elapsed >= self._idle_threshold

    async def generate(
        self,
        messages: list[Message],
        *,
        session_memory: str | None = None,
    ) -> str | None:
        """Generate and cache the away summary."""
        summary = await generate_away_summary(
            messages,
            self._provider,
            session_memory=session_memory,
        )
        if summary:
            self._last_summary = summary
            self._summary_shown = True
        return summary

    @property
    def last_summary(self) -> str | None:
        return self._last_summary


# ── Session memory integration ──────────────────────────────────────


def _get_session_memory_context(conversation_id: str | None = None) -> str | None:
    """Auto-read session memory content if available.

    Wraps the session memory service so callers don't need to import it
    directly.  Returns None silently on any failure.
    Pass *conversation_id* when multiple sessions may share the process.
    """
    try:
        from .session_memory import get_session_memory_content
        return get_session_memory_content(conversation_id)
    except Exception:
        return None


# ── Fast model selection ────────────────────────────────────────────


def _get_summary_model(
    default_provider: BaseProvider,
    *,
    config_model: str = "",
) -> BaseProvider:
    """Return a fast/cheap provider for summary generation.

    Checks ``away_summary.model`` in project config, then falls back
    to the default provider.  This keeps summary calls inexpensive
    (haiku-class).
    """
    if config_model:
        try:
            clone = default_provider.with_model(config_model)
            return clone
        except Exception:
            logger.debug(
                "Configured away_summary model %r not available, using default",
                config_model,
            )

    try:
        fast = default_provider.get_fast_variant()
        if fast is not None:
            return fast
    except (AttributeError, NotImplementedError):
        pass

    return default_provider


# ── Abort support ───────────────────────────────────────────────────


async def generate_away_summary_with_abort(
    messages: list[Message],
    provider: BaseProvider,
    *,
    session_memory: str | None = None,
    abort_signal: asyncio.Event | None = None,
    config_model: str = "",
    conversation_id: str | None = None,
) -> str | None:
    """Like :func:`generate_away_summary` but accepts an ``abort_signal``.

    The ``asyncio.Event`` is checked before and during generation.
    Passing *config_model* selects a fast model override.
    Pass *conversation_id* when loading session memory in a multi-session process.
    """
    if abort_signal and abort_signal.is_set():
        return None

    if session_memory is None:
        session_memory = _get_session_memory_context(conversation_id)

    effective_provider = _get_summary_model(provider, config_model=config_model)

    recent = messages[-RECENT_MESSAGE_WINDOW:]
    conv_parts: list[str] = []
    for msg in recent:
        text = msg.text.strip()[:2000]
        if text:
            conv_parts.append(f"[{msg.role.upper()}]: {text}")
    conversation = "\n\n".join(conv_parts)

    prompt = _build_prompt(session_memory)
    full_prompt = f"{conversation}\n\n---\n\n{prompt}"

    if abort_signal and abort_signal.is_set():
        return None

    try:
        result = await run_forked_side_query(
            provider=effective_provider,
            parent_messages=recent,
            system_prompt=AWAY_SUMMARY_SYSTEM,
            prompt=full_prompt,
            max_tokens=256,
            temperature=0.0,
            query_source="away_summary",
        )

        if abort_signal and abort_signal.is_set():
            return None

        summary = result.strip()
        return summary or None
    except Exception as exc:
        logger.debug("Away summary generation failed: %s", exc)
        return None


# ── Summary caching ─────────────────────────────────────────────────


class _SummaryCache:
    """Cache the last away summary keyed by message count.

    Avoids redundant LLM calls when the conversation hasn't changed.
    """

    def __init__(self) -> None:
        self._cached_summary: str | None = None
        self._message_count: int = -1

    def get(self, message_count: int) -> str | None:
        """Return cached summary if *message_count* matches, else None."""
        if message_count == self._message_count and self._cached_summary:
            return self._cached_summary
        return None

    def put(self, message_count: int, summary: str) -> None:
        self._message_count = message_count
        self._cached_summary = summary

    def invalidate(self) -> None:
        """Force regeneration on next call."""
        self._cached_summary = None
        self._message_count = -1


_summary_cache = _SummaryCache()


async def generate_away_summary_cached(
    messages: list[Message],
    provider: BaseProvider,
    *,
    session_memory: str | None = None,
    abort_signal: asyncio.Event | None = None,
    config_model: str = "",
    conversation_id: str | None = None,
    force: bool = False,
) -> str | None:
    """Generate an away summary, returning a cached result when possible.

    Set *force=True* or call ``_summary_cache.invalidate()`` to skip
    the cache.
    """
    count = len(messages)
    if not force:
        cached = _summary_cache.get(count)
        if cached is not None:
            return cached

    summary = await generate_away_summary_with_abort(
        messages,
        provider,
        session_memory=session_memory,
        abort_signal=abort_signal,
        config_model=config_model,
        conversation_id=conversation_id,
    )
    if summary:
        _summary_cache.put(count, summary)
    return summary
