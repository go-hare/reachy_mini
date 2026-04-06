"""Post-sampling companion reactions — small quips after assistant text (TS ``companionReaction``-style)."""

from __future__ import annotations

import hashlib
import inspect
from typing import TYPE_CHECKING, Callable, Awaitable

from ..hooks import PostSamplingContext, PostSamplingHook
from .companion import _read_global_config, get_companion

if TYPE_CHECKING:
    from ..agent import Agent


_QUIPS = (
    "Nice one!",
    "Keep going~",
    "Interesting…",
    "Got it.",
    "Hmm!",
    "✨",
)


class CompanionObserver(PostSamplingHook):
    """Occasionally sets a short bubble line from assistant output hash (no extra LLM)."""

    def __init__(
        self,
        *,
        user_id: str,
        on_reaction: Callable[[str | None], Awaitable[None] | None] | None = None,
    ) -> None:
        self._user_id = user_id
        self._on_reaction = on_reaction

    async def on_post_sampling(
        self,
        context: PostSamplingContext,
        *,
        agent: Agent,
    ) -> None:
        c = get_companion(self._user_id)
        if _read_global_config().get("companionMuted") is True or c is None or not context.reply_text.strip():
            return
        h = int(hashlib.sha256(context.reply_text.encode()).hexdigest(), 16)
        if h % 7 != 0:
            return
        quip = _QUIPS[h % len(_QUIPS)]
        if self._on_reaction is None:
            return
        r = self._on_reaction(quip)
        if inspect.isawaitable(r):
            await r


__all__ = ["CompanionObserver"]
