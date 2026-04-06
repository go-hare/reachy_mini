"""Unified context injection bus (attachments system).

All extra context — file references, images, memory snippets, skill
listings, MCP resources, task status — flows through this bus and
gets injected into the conversation as user/attachment messages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .messages import Message, user_message

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Attachment:
    """A single piece of contextual information to inject."""
    type: str       # "file", "image", "memory", "skill", "mcp_resource", "task", ...
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AttachmentCollector:
    """Collect attachments from various sources and inject them.

    Sources register themselves via ``add_source``.  Before each query,
    ``collect()`` gathers all attachments and ``inject()`` appends them
    as user messages.
    """

    def __init__(self) -> None:
        self._sources: list[AttachmentSource] = []

    def add_source(self, source: AttachmentSource) -> None:
        self._sources.append(source)

    async def collect(self, context: dict[str, Any]) -> list[Attachment]:
        """Gather attachments from all registered sources."""
        attachments: list[Attachment] = []
        for source in self._sources:
            try:
                items = await source.get_attachments(context)
                attachments.extend(items)
            except Exception:
                logger.debug("Attachment source %s failed", type(source).__name__, exc_info=True)
        return attachments

    def inject(
        self,
        attachments: list[Attachment],
        messages: list[Message],
    ) -> list[Message]:
        """Append attachment content as user messages.

        ``companion_intro`` matches ``normalizeAttachmentForAPI`` / ``buddy/prompt.ts``:
        a single meta user message with ``companion_intro_text`` body, not the generic
        ``[Context attachments]`` bundle.
        """
        if not attachments:
            return messages

        result = list(messages)
        bundle_parts: list[str] = []
        for att in attachments:
            if att.type == "companion_intro":
                name = str(att.metadata.get("name", ""))
                result.append(
                    user_message(
                        att.content,
                        isMeta=True,
                        companion_intro_name=name,
                    ),
                )
            else:
                label = att.type.upper()
                bundle_parts.append(f"[{label}] {att.content}")

        if bundle_parts:
            attachment_text = "\n\n".join(bundle_parts)
            result.append(
                user_message(
                    f"[Context attachments]\n{attachment_text}",
                    _attachment=True,
                ),
            )
        return result


class AttachmentSource:
    """Base class for attachment providers."""

    async def get_attachments(self, context: dict[str, Any]) -> list[Attachment]:
        return []


class CompanionIntroSource(AttachmentSource):
    """``getCompanionIntroAttachment`` from ``buddy/prompt.ts``, attached via ``query.ts`` bus.

    Context (see ``engine/query.py``): ``messages``, ``buddy_enabled``, ``companion_muted``
    (optional override; ``None`` reads ``companionMuted`` from global config).
    """

    async def get_attachments(self, context: dict[str, Any]) -> list[Attachment]:
        from .buddy.prompt import companion_intro_text, get_companion_intro_attachment

        messages = context.get("messages")
        if messages is None:
            return []
        buddy_enabled = bool(context.get("buddy_enabled", True))
        raw = get_companion_intro_attachment(
            messages,
            buddy_enabled=buddy_enabled,
            companion_muted=context.get("companion_muted"),
        )
        if not raw:
            return []
        a = raw[0]
        text = companion_intro_text(a["name"], a["species"])
        return [
            Attachment(
                type="companion_intro",
                content=text,
                metadata={"name": a["name"], "species": a["species"]},
            )
        ]


def ensure_companion_intro_source(collector: AttachmentCollector | None) -> None:
    """Register :class:`CompanionIntroSource` once (idempotent)."""
    if collector is None:
        return
    if any(type(s).__name__ == "CompanionIntroSource" for s in collector._sources):
        return
    collector.add_source(CompanionIntroSource())


class MemoryAttachmentSource(AttachmentSource):
    """Inject relevant memories as attachments."""

    def __init__(self, memory_adapter: Any) -> None:
        self._adapter = memory_adapter

    async def get_attachments(self, context: dict[str, Any]) -> list[Attachment]:
        conv_id = context.get("conversation_id", "")
        query = context.get("user_text", "")
        if not query:
            return []
        results = self._adapter.find_relevant_memories(conv_id, query)
        return [
            Attachment(type="memory", content=r.get("content", ""), metadata=r.get("metadata", {}))
            for r in results
            if r.get("content")
        ]
