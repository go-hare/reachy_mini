"""Skill prefetch — start skill discovery before the main API call.

Mirrors Claude Code's ``startSkillDiscoveryPrefetch`` + ``startRelevantMemoryPrefetch``:
the query loop kicks off skill/memory search as soon as the user message
arrives, then collects results right before sending to the LLM.  This
hides latency behind the main turn's processing time.

Usage::

    prefetch = SkillPrefetch(skill_loader, memory_provider)
    handle = prefetch.start(user_text, messages)

    # ... do other work (attachment collection, etc.) ...

    attachments = await handle.collect()
    # inject attachments into the message list
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..skills import SkillLoader
    from ..providers import BaseProvider
    from ..messages import Message

logger = logging.getLogger(__name__)


@dataclass
class PrefetchHandle:
    """An in-flight prefetch operation.  Call ``collect()`` to await results."""

    _skill_task: asyncio.Task[list[Any]] | None = None
    _memory_task: asyncio.Task[list[Any]] | None = None
    settled: bool = False

    async def collect(self, timeout: float = 5.0) -> list[Any]:
        """Await all prefetch tasks and return combined attachments.

        Times out gracefully — partial results are better than blocking.
        """
        attachments: list[Any] = []

        tasks = []
        if self._skill_task is not None:
            tasks.append(self._skill_task)
        if self._memory_task is not None:
            tasks.append(self._memory_task)

        if not tasks:
            self.settled = True
            return attachments

        try:
            done, pending = await asyncio.wait(tasks, timeout=timeout)
        except Exception:
            self.settled = True
            return attachments

        for task in done:
            try:
                result = task.result()
                if result:
                    attachments.extend(result)
            except Exception as exc:
                logger.debug("Prefetch task failed: %s", exc)

        for task in pending:
            task.cancel()

        self.settled = True
        return attachments


class SkillPrefetch:
    """Manages async prefetch of skills and memories.

    Args:
        skill_loader: The SkillLoader instance to search for relevant skills.
        memory_provider: Optional provider for memory retrieval side-queries.
            When set, a lightweight LLM call extracts search terms from the
            user message and retrieves relevant memories.
    """

    def __init__(
        self,
        skill_loader: SkillLoader | None = None,
        memory_provider: BaseProvider | None = None,
    ) -> None:
        self._skill_loader = skill_loader
        self._memory_provider = memory_provider

    def start(
        self,
        user_text: str,
        messages: list[Message] | None = None,
    ) -> PrefetchHandle:
        """Start prefetch tasks and return a handle.

        Call immediately when the user message arrives — before other
        processing — to maximize overlap.
        """
        handle = PrefetchHandle()

        if self._skill_loader is not None and user_text.strip():
            handle._skill_task = asyncio.ensure_future(
                self._prefetch_skills(user_text)
            )

        if self._memory_provider is not None and user_text.strip():
            handle._memory_task = asyncio.ensure_future(
                self._prefetch_memories(user_text, messages)
            )

        return handle

    async def _prefetch_skills(self, user_text: str) -> list[Any]:
        """Find relevant skills for the user message."""
        from ..attachments import Attachment

        if self._skill_loader is None:
            return []

        matched = self._skill_loader.match(user_text, max_skills=3)
        if not matched:
            return []

        rendered = self._skill_loader.render(matched)
        return [Attachment(
            type="prefetched_skill",
            content=rendered,
            metadata={"skill_names": [s.name for s in matched]},
        )]

    async def _prefetch_memories(
        self,
        user_text: str,
        messages: list[Message] | None = None,
    ) -> list[Any]:
        """Retrieve relevant memories using a side-query for term extraction."""
        from ..attachments import Attachment

        if self._memory_provider is None:
            return []

        if not user_text or len(user_text.split()) < 2:
            return []

        try:
            from ..delegation.fork import run_forked_side_query

            terms = await run_forked_side_query(
                provider=self._memory_provider,
                parent_messages=messages or [],
                system_prompt=(
                    "Extract 2-5 search terms from the user message "
                    "that would help find relevant memories or context. "
                    "Return just the terms, comma-separated."
                ),
                prompt=user_text,
                max_tokens=50,
                temperature=0.0,
                query_source="skill_prefetch_memory_terms",
            )

            if terms.strip():
                return [Attachment(
                    type="memory_search_terms",
                    content=terms.strip(),
                    metadata={"source": "prefetch"},
                )]
        except Exception as exc:
            logger.debug("Memory prefetch failed: %s", exc)

        return []
