"""Optional warm-read of session memory before the main API call.

Parity hook for ``startRelevantMemoryPrefetch`` (reference ``query.ts``): when
``MINI_AGENT_RELEVANT_MEMORY_PREFETCH=1``, load the session-memory markdown for
this conversation (if present) on a worker thread so the file cache is warm.
"""

from __future__ import annotations

import asyncio
import logging
import os
logger = logging.getLogger(__name__)

_ENV = "MINI_AGENT_RELEVANT_MEMORY_PREFETCH"


def is_relevant_memory_prefetch_enabled() -> bool:
    v = (os.environ.get(_ENV) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


async def await_relevant_memory_prefetch_if_enabled(conversation_id: str) -> None:
    if not is_relevant_memory_prefetch_enabled() or not (conversation_id or "").strip():
        return
    try:
        from .session_memory import get_memory_path

        path = get_memory_path(conversation_id)
        if not path.exists():
            return

        def _read() -> None:
            path.read_text(encoding="utf-8")

        await asyncio.to_thread(_read)
    except Exception:
        logger.debug("relevant memory prefetch failed", exc_info=True)


__all__ = ["await_relevant_memory_prefetch_if_enabled", "is_relevant_memory_prefetch_enabled"]
