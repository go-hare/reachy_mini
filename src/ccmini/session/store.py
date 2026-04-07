"""Session transcript persistence."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..messages import (
    ContentBlock,
    DocumentBlock,
    ImageBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ..paths import mini_agent_path

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SessionInfo:
    session_id: str
    updated_at: str
    title: str = ""
    preview: str = ""
    message_count: int = 0
    cwd: str = ""
    last_stop_reason: str = ""
    turn_phase: str = ""


@dataclass(slots=True)
class SessionMetadata:
    session_id: str = ""
    title: str = ""
    cwd: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    message_count: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    model: str = ""
    parent_session: str = ""
    last_stop_reason: str = ""
    turn_phase: str = ""
    pending_run_id: str = ""
    pending_tool_count: int = 0


class SessionStore:
    """JSONL transcript store plus minimal sidecar metadata."""

    def __init__(self, session_dir: Path | str | None = None) -> None:
        self.session_dir = Path(session_dir or mini_agent_path("sessions"))
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in session_id)
        return self.session_dir / f"{safe}.jsonl"

    def _meta_path(self, session_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in session_id)
        return self.session_dir / f"{safe}.meta.json"

    def save_messages(self, session_id: str, messages: list[Message]) -> None:
        path = self._session_path(session_id)
        with path.open("w", encoding="utf-8") as handle:
            for message in messages:
                handle.write(json.dumps(_message_to_dict(message), ensure_ascii=False) + "\n")

    def save_metadata(self, meta: SessionMetadata) -> None:
        path = self._meta_path(meta.session_id)
        data = {
            "session_id": meta.session_id,
            "title": meta.title,
            "cwd": meta.cwd,
            "tags": meta.tags,
            "created_at": meta.created_at,
            "updated_at": meta.updated_at,
            "message_count": meta.message_count,
            "total_tokens": meta.total_tokens,
            "total_cost_usd": meta.total_cost_usd,
            "model": meta.model,
            "parent_session": meta.parent_session,
            "last_stop_reason": meta.last_stop_reason,
            "turn_phase": meta.turn_phase,
            "pending_run_id": meta.pending_run_id,
            "pending_tool_count": meta.pending_tool_count,
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def load_session(self, session_id: str) -> list[Message]:
        path = self._session_path(session_id)
        if not path.exists():
            return []

        messages: list[Message] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(_dict_to_message(json.loads(line)))
                except (json.JSONDecodeError, KeyError) as exc:
                    logger.warning("Skipping bad JSON in session %s: %s", session_id, exc)
        return messages

    def load_metadata(self, session_id: str) -> SessionMetadata | None:
        path = self._meta_path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return SessionMetadata(
            **{key: value for key, value in data.items() if key in SessionMetadata.__dataclass_fields__}
        )

    def list_sessions(self, *, limit: int = 0) -> list[SessionInfo]:
        sessions: list[SessionInfo] = []
        for path in self.session_dir.glob("*.jsonl"):
            if path.name.startswith("_"):
                continue
            stat = path.stat()
            session_id = path.stem
            meta = self.load_metadata(session_id)
            message_count = meta.message_count if meta is not None else 0
            preview = ""
            title = ""
            cwd = ""
            last_stop_reason = ""
            turn_phase = ""
            updated_at_ts = stat.st_mtime

            if meta is not None:
                title = meta.title
                cwd = meta.cwd
                message_count = meta.message_count or message_count
                last_stop_reason = meta.last_stop_reason
                turn_phase = meta.turn_phase
                if meta.updated_at:
                    updated_at_ts = meta.updated_at

            if not title or not preview or not message_count:
                messages = self.load_session(session_id)
                if not message_count:
                    message_count = len(messages)
                preview = _build_session_preview(messages)

            sessions.append(
                SessionInfo(
                    session_id=session_id,
                    updated_at=datetime.fromtimestamp(updated_at_ts).isoformat(),
                    title=title,
                    preview=preview,
                    message_count=message_count,
                    cwd=cwd,
                    last_stop_reason=last_stop_reason,
                    turn_phase=turn_phase,
                )
            )
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        if limit > 0:
            sessions = sessions[:limit]
        return sessions

    def get_latest_session(self) -> str | None:
        """Return the most recently updated session id, if any."""
        sessions = self.list_sessions(limit=1)
        if not sessions:
            return None
        return sessions[0].session_id

    def session_exists(self, session_id: str) -> bool:
        return self._session_path(session_id).exists() or self._meta_path(session_id).exists()


def _message_to_dict(message: Message) -> dict[str, Any]:
    result: dict[str, Any] = {
        "role": message.role,
        "uuid": message.metadata.get("uuid", ""),
        "timestamp": message.metadata.get("timestamp", ""),
        "type": message.metadata.get("type", message.role),
    }
    if message.name is not None:
        result["name"] = message.name
    if isinstance(message.content, str):
        result["content"] = message.content
    else:
        result["content"] = [_block_to_dict(block) for block in message.content]

    # Persist transcript-visible fields at top level, matching the TS transcript shape.
    for key in (
        "isMeta",
        "isVisibleInTranscriptOnly",
        "isCompactSummary",
        "isVirtual",
        "sourceToolAssistantUUID",
        "toolUseResult",
        "mcpMeta",
        "origin",
        "imagePasteIds",
        "permissionMode",
        "assistantId",
        "requestId",
        "stop_reason",
        "model",
    ):
        if key in message.metadata:
            result[key] = message.metadata[key]

    if message.metadata:
        # Store all remaining metadata except values lifted to the top level.
        extra_meta = {
            k: v for k, v in message.metadata.items()
            if k not in (
                "uuid",
                "timestamp",
                "type",
                "isMeta",
                "isVisibleInTranscriptOnly",
                "isCompactSummary",
                "isVirtual",
                "sourceToolAssistantUUID",
                "toolUseResult",
                "mcpMeta",
                "origin",
                "imagePasteIds",
                "permissionMode",
                "assistantId",
                "requestId",
                "stop_reason",
                "model",
            )
        }
        if extra_meta:
            result["metadata"] = extra_meta
    return result


def _block_to_dict(block: ContentBlock) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
            "metadata": dict(block.metadata),
        }
    if isinstance(block, ImageBlock):
        return {"type": "image", "source": block.source, "media_type": block.media_type}
    if isinstance(block, DocumentBlock):
        return {"type": "document", "source": block.source, "media_type": block.media_type}
    return {"type": "unknown"}


def _dict_to_message(data: dict[str, Any]) -> Message:
    role = data["role"]
    raw_content = data["content"]
    metadata = dict(data.get("metadata", {}))
    # Restore top-level transcript fields into metadata.
    for key in (
        "uuid",
        "timestamp",
        "type",
        "isMeta",
        "isVisibleInTranscriptOnly",
        "isCompactSummary",
        "isVirtual",
        "sourceToolAssistantUUID",
        "toolUseResult",
        "mcpMeta",
        "origin",
        "imagePasteIds",
        "permissionMode",
        "assistantId",
        "requestId",
        "stop_reason",
        "model",
    ):
        if key in data:
            metadata[key] = data[key]

    if isinstance(raw_content, str):
        return Message(role=role, content=raw_content, name=data.get("name"), metadata=metadata)

    blocks: list[ContentBlock] = []
    for item in raw_content:
        block_type = item.get("type", "")
        if block_type == "text":
            blocks.append(TextBlock(text=item["text"]))
        elif block_type == "tool_use":
            blocks.append(ToolUseBlock(id=item["id"], name=item["name"], input=item["input"]))
        elif block_type == "tool_result":
            blocks.append(
                ToolResultBlock(
                    tool_use_id=item["tool_use_id"],
                    content=item["content"],
                    is_error=item.get("is_error", False),
                    metadata=dict(item.get("metadata", {})),
                )
            )
        elif block_type == "image":
            blocks.append(ImageBlock(source=item["source"], media_type=item.get("media_type", "image/png")))
        elif block_type == "document":
            blocks.append(
                DocumentBlock(
                    source=item["source"],
                    media_type=item.get("media_type", "application/pdf"),
                )
            )
    return Message(role=role, content=blocks, name=data.get("name"), metadata=metadata)


def get_session_transcript_path(session_id: str, session_dir: Path | str | None = None) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in session_id)
    base_dir = Path(session_dir) if session_dir is not None else mini_agent_path("sessions")
    return base_dir / f"{safe}.jsonl"


def _build_session_preview(messages: list[Message]) -> str:
    for message in messages:
        text = message.text.strip()
        if not text:
            continue
        if str(message.metadata.get("isMeta", "")).lower() == "true" or message.metadata.get("isMeta") is True:
            continue
        if message.metadata.get("isCompactSummary"):
            continue
        if message.metadata.get("isVisibleInTranscriptOnly"):
            continue
        compact = " ".join(text.split())
        if compact:
            return compact[:120] + ("..." if len(compact) > 120 else "")
    return "(session)"
