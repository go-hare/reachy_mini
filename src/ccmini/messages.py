"""Provider-agnostic message types for the mini-agent engine.

All LLM providers (Anthropic, OpenAI, OpenAI-compatible) convert to/from
these internal types.  The design mirrors Anthropic's content-block model
because it maps cleanly to tool_use / tool_result patterns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Literal
from uuid import uuid4


# ---------------------------------------------------------------------------
# Content blocks
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TextBlock:
    text: str
    type: Literal["text"] = "text"


@dataclass(slots=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: Literal["tool_use"] = "tool_use"


@dataclass(slots=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"


@dataclass(slots=True)
class ImageBlock:
    source: str
    media_type: str = "image/png"
    type: Literal["image"] = "image"


@dataclass(slots=True)
class DocumentBlock:
    source: str
    media_type: str = "application/pdf"
    type: Literal["document"] = "document"


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock | ImageBlock | DocumentBlock


# Synthetic tool_result content inserted when a tool_use block has no matching
# tool_result. This mirrors the reference placeholder so downstream filtering
# and diagnostics can identify fake pairing reliably.
SYNTHETIC_TOOL_RESULT_PLACEHOLDER = "[Tool result missing due to internal error]"
NO_CONTENT_MESSAGE = "No response requested."
ORPHANED_TOOL_RESULT_REMOVED_MESSAGE = "[Orphaned tool result removed due to conversation resume]"
TOOL_USE_INTERRUPTED_MESSAGE = "[Tool use interrupted]"
SYNTHETIC_MODEL = "<synthetic>"
SYNTHETIC_MESSAGES = {
    NO_CONTENT_MESSAGE,
    ORPHANED_TOOL_RESULT_REMOVED_MESSAGE,
    TOOL_USE_INTERRUPTED_MESSAGE,
}


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Message:
    """A single turn in the conversation."""

    role: Literal["user", "assistant", "system"]
    content: list[ContentBlock] | str
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        parts = []
        for block in self.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
            elif isinstance(block, ImageBlock):
                parts.append("[image]")
            elif isinstance(block, DocumentBlock):
                parts.append("[document]")
        return "\n".join(parts)

    @property
    def tool_use_blocks(self) -> list[ToolUseBlock]:
        if isinstance(self.content, str):
            return []
        return [b for b in self.content if isinstance(b, ToolUseBlock)]

    @property
    def tool_result_blocks(self) -> list[ToolResultBlock]:
        if isinstance(self.content, str):
            return []
        return [b for b in self.content if isinstance(b, ToolResultBlock)]

    @property
    def has_tool_use(self) -> bool:
        return len(self.tool_use_blocks) > 0


def is_synthetic_message(message: Message) -> bool:
    if message.role == "system":
        return False
    if isinstance(message.content, str):
        return message.content in SYNTHETIC_MESSAGES
    if not message.content:
        return False
    first = message.content[0]
    return isinstance(first, TextBlock) and first.text in SYNTHETIC_MESSAGES


def is_synthetic_api_error_message(message: Message) -> bool:
    return (
        message.role == "assistant"
        and bool(message.metadata.get("isApiErrorMessage") or message.metadata.get("isSyntheticApiError"))
        and str(message.metadata.get("model", "")) == SYNTHETIC_MODEL
    )


# ---------------------------------------------------------------------------
# Message constructors
# ---------------------------------------------------------------------------

def user_message(text: str, **metadata: Any) -> Message:
    if "uuid" not in metadata:
        metadata["uuid"] = uuid4().hex
    if "timestamp" not in metadata:
        metadata["timestamp"] = time.time()
    return Message(role="user", content=text, metadata=metadata)


def assistant_message(content: list[ContentBlock] | str, **metadata: Any) -> Message:
    if "uuid" not in metadata:
        metadata["uuid"] = uuid4().hex
    if "assistantId" not in metadata:
        metadata["assistantId"] = uuid4().hex
    if "timestamp" not in metadata:
        metadata["timestamp"] = time.time()
    return Message(role="assistant", content=content, metadata=metadata)


def system_message(text: str, **metadata: Any) -> Message:
    if "uuid" not in metadata:
        metadata["uuid"] = uuid4().hex
    if "timestamp" not in metadata:
        metadata["timestamp"] = time.time()
    return Message(role="system", content=text, metadata=metadata)


def tool_result_message(results: list[ToolResultBlock]) -> Message:
    return Message(
        role="user",
        content=results,
        metadata={"uuid": uuid4().hex, "timestamp": time.time()},
    )


# ---------------------------------------------------------------------------
# Stream events yielded by the query loop
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TextEvent:
    """Incremental text chunk from the model."""
    text: str
    type: Literal["text"] = "text"


@dataclass(slots=True)
class ToolCallEvent:
    """Model requested a tool call."""
    tool_use_id: str
    tool_name: str
    tool_input: dict[str, Any]
    type: Literal["tool_call"] = "tool_call"


@dataclass(slots=True)
class ToolResultEvent:
    """Result of a tool execution.

    ``tool_name`` defaults to empty for synthetic events (e.g. interrupt) where
    the name is unknown or redundant.
    """
    tool_use_id: str
    result: str
    tool_name: str = ""
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    type: Literal["tool_result"] = "tool_result"


@dataclass(slots=True)
class RequestStartEvent:
    """Emitted at the start of each API request iteration — mirrors TS stream_request_start."""
    type: Literal["stream_request_start"] = "stream_request_start"


@dataclass(slots=True)
class CompletionEvent:
    """Final reply from the model (full text)."""
    text: str
    conversation_id: str = ""
    usage: Any = None
    stop_reason: str | None = None
    type: Literal["completion"] = "completion"


@dataclass(slots=True)
class ErrorEvent:
    """An error during query processing."""
    error: str
    recoverable: bool = False
    type: Literal["error"] = "error"


@dataclass(slots=True)
class IdleEvent:
    """Idle tick in resident mode."""
    idle_seconds: float = 0.0
    type: Literal["idle"] = "idle"


@dataclass(slots=True)
class PendingToolCallEvent:
    """Client-side tool call awaiting external results."""
    run_id: str
    calls: list[ToolCallEvent]
    type: Literal["pending_tool_call"] = "pending_tool_call"


@dataclass(slots=True)
class UsageEvent:
    """Token usage from a single LLM call, emitted at end of stream."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    model: str = ""
    stop_reason: str | None = None
    type: Literal["usage"] = "usage"


@dataclass(slots=True)
class ThinkingEvent:
    """Reasoning/thinking status or delta emitted during a model stream."""

    text: str = ""
    is_redacted: bool = False
    phase: Literal["start", "delta", "end"] = "delta"
    source: Literal["status", "model"] = "model"
    signature: str = ""
    type: Literal["thinking"] = "thinking"


@dataclass(slots=True)
class ToolProgressEvent:
    """Incremental progress from a running tool (e.g. stdout lines from bash).

    Emitted by tools that support streaming execution via ``stream_execute``.
    Unlike ``ToolResultEvent`` (final), this may be emitted many times.
    """
    tool_use_id: str
    tool_name: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    type: Literal["tool_progress"] = "tool_progress"


@dataclass(slots=True)
class ToolUseSummaryEvent:
    """Human-readable one-line summary of a tool execution batch.

    Generated asynchronously by a fast model after tool calls complete.
    Non-critical — hosts can display it as a progress indicator.
    """
    summary: str
    tool_use_ids: list[str] = field(default_factory=list)
    type: Literal["tool_use_summary"] = "tool_use_summary"


StreamEvent = (
    TextEvent
    | ToolCallEvent
    | ToolResultEvent
    | CompletionEvent
    | ErrorEvent
    | IdleEvent
    | PendingToolCallEvent
    | UsageEvent
    | ThinkingEvent
    | ToolProgressEvent
    | ToolUseSummaryEvent
)


def is_compact_boundary_message(message: Message) -> bool:
    return (
        message.role == "system"
        and str(message.metadata.get("subtype", "")) == "compact_boundary"
    )


def get_messages_after_compact_boundary(messages: list[Message]) -> list[Message]:
    last_boundary = -1
    for index, message in enumerate(messages):
        if is_compact_boundary_message(message):
            last_boundary = index
    if last_boundary == -1:
        return list(messages)
    return list(messages[last_boundary:])


def _content_blocks(content: list[ContentBlock] | str) -> list[ContentBlock]:
    if isinstance(content, list):
        return list(content)
    if not content:
        return []
    return [TextBlock(text=content)]


def _ensure_tool_result_pairing(messages: list[Message]) -> list[Message]:
    """Repair tool_use/tool_result adjacency mismatches before the API call."""
    result: list[Message] = []
    all_seen_tool_use_ids: set[str] = set()
    i = 0

    while i < len(messages):
        msg = messages[i]

        if msg.role != "assistant":
            if msg.role == "user" and (not result or result[-1].role != "assistant"):
                content = _content_blocks(msg.content)
                stripped = [block for block in content if not isinstance(block, ToolResultBlock)]
                if len(stripped) != len(content):
                    if stripped:
                        result.append(Message(role="user", content=stripped, metadata=msg.metadata))
                    elif not result:
                        result.append(user_message(ORPHANED_TOOL_RESULT_REMOVED_MESSAGE))
                    i += 1
                    continue
            result.append(msg)
            i += 1
            continue

        assistant_blocks = _content_blocks(msg.content)
        repaired_assistant: list[ContentBlock] = []
        tool_use_ids: list[str] = []
        for block in assistant_blocks:
            if isinstance(block, ToolUseBlock):
                if block.id in all_seen_tool_use_ids:
                    continue
                all_seen_tool_use_ids.add(block.id)
                tool_use_ids.append(block.id)
            repaired_assistant.append(block)

        if not repaired_assistant:
            repaired_assistant = [TextBlock(text=TOOL_USE_INTERRUPTED_MESSAGE)]

        result.append(Message(role="assistant", content=repaired_assistant, metadata=msg.metadata))

        next_msg = messages[i + 1] if i + 1 < len(messages) else None
        if next_msg is None or next_msg.role != "user":
            if tool_use_ids:
                synthetic = [
                    ToolResultBlock(
                        tool_use_id=tool_use_id,
                        content=SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
                        is_error=True,
                    )
                    for tool_use_id in tool_use_ids
                ]
                result.append(
                    Message(
                        role="user",
                        content=synthetic,
                        metadata={"uuid": uuid4().hex, "timestamp": time.time(), "isMeta": True},
                    )
                )
            i += 1
            continue

        next_blocks = _content_blocks(next_msg.content)
        existing_tool_result_ids: set[str] = set()
        has_duplicate_tool_results = False
        for block in next_blocks:
            if not isinstance(block, ToolResultBlock):
                continue
            if block.tool_use_id in existing_tool_result_ids:
                has_duplicate_tool_results = True
            existing_tool_result_ids.add(block.tool_use_id)

        tool_use_id_set = set(tool_use_ids)
        missing_ids = [
            tool_use_id for tool_use_id in tool_use_ids if tool_use_id not in existing_tool_result_ids
        ]
        orphaned_ids = [
            tool_result_id
            for tool_result_id in existing_tool_result_ids
            if tool_result_id not in tool_use_id_set
        ]
        synthetic_blocks = [
            ToolResultBlock(
                tool_use_id=tool_use_id,
                content=SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
                is_error=True,
            )
            for tool_use_id in missing_ids
        ]

        kept_user_blocks = list(next_blocks)
        if orphaned_ids or has_duplicate_tool_results:
            orphaned_set = set(orphaned_ids)
            seen_tr_ids: set[str] = set()
            filtered_blocks: list[ContentBlock] = []
            for block in kept_user_blocks:
                if isinstance(block, ToolResultBlock):
                    if block.tool_use_id in orphaned_set:
                        continue
                    if block.tool_use_id in seen_tr_ids:
                        continue
                    seen_tr_ids.add(block.tool_use_id)
                filtered_blocks.append(block)
            kept_user_blocks = filtered_blocks

        patched_user_blocks = [*synthetic_blocks, *kept_user_blocks]

        if patched_user_blocks:
            result.append(Message(role="user", content=patched_user_blocks, metadata=next_msg.metadata))
        else:
            result.append(user_message(NO_CONTENT_MESSAGE, isMeta=True))

        i += 2

    return result


# ---------------------------------------------------------------------------
# normalizeMessagesForAPI — 1:1 port of TS utils/messages.ts
# ---------------------------------------------------------------------------

def _merge_user_message_metadata(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    merged = dict(previous)
    previous_is_meta = bool(previous.get("isMeta"))
    current_is_meta = bool(current.get("isMeta"))

    if previous_is_meta and not current_is_meta:
        merged.update(current)
    else:
        for key, value in current.items():
            merged.setdefault(key, value)

    merged["uuid"] = current.get("uuid") if previous_is_meta and current.get("uuid") else previous.get("uuid")
    merged["timestamp"] = current.get("timestamp") if previous_is_meta and current.get("timestamp") else previous.get("timestamp")

    if previous_is_meta and current_is_meta:
        merged["isMeta"] = True
    else:
        merged.pop("isMeta", None)

    return merged


def _api_error_block_types_to_strip(message: Message) -> set[type[ContentBlock]]:
    text = message.text.lower()
    if not text:
        return set()
    if "request too large" in text:
        return {ImageBlock, DocumentBlock}
    if "image" in text and "too large" in text:
        return {ImageBlock}
    if "pdf" in text and (
        "too large" in text or "password" in text or "invalid" in text
    ):
        return {DocumentBlock}
    return set()


def _assistant_has_only_whitespace_text(content: list[ContentBlock]) -> bool:
    if not content:
        return False
    for block in content:
        if not isinstance(block, TextBlock):
            return False
        if block.text.strip():
            return False
    return True


def _filter_whitespace_only_assistant_messages(messages: list[Message]) -> list[Message]:
    filtered = [
        message for message in messages
        if not (
            message.role == "assistant"
            and isinstance(message.content, list)
            and _assistant_has_only_whitespace_text(message.content)
        )
    ]
    if len(filtered) == len(messages):
        return messages

    merged: list[Message] = []
    for message in filtered:
        prev = merged[-1] if merged else None
        if prev is not None and prev.role == "user" and message.role == "user":
            prev_content = _content_blocks(prev.content)
            new_content = _content_blocks(message.content)
            merged[-1] = Message(
                role="user",
                content=prev_content + new_content,
                metadata=_merge_user_message_metadata(prev.metadata, message.metadata),
            )
        else:
            merged.append(message)
    return merged


def _ensure_non_empty_assistant_content(messages: list[Message]) -> list[Message]:
    if not messages:
        return messages
    updated: list[Message] = []
    changed = False
    for index, message in enumerate(messages):
        if (
            message.role == "assistant"
            and index != len(messages) - 1
            and isinstance(message.content, list)
            and len(message.content) == 0
        ):
            updated.append(
                Message(
                    role="assistant",
                    content=[TextBlock(text=NO_CONTENT_MESSAGE)],
                    name=message.name,
                    metadata=message.metadata,
                )
            )
            changed = True
        else:
            updated.append(message)
    return updated if changed else messages


def _strip_problematic_blocks_after_api_errors(messages: list[Message]) -> list[Message]:
    strip_targets: dict[str, set[type[ContentBlock]]] = {}
    for index, message in enumerate(messages):
        if not is_synthetic_api_error_message(message):
            continue
        block_types = _api_error_block_types_to_strip(message)
        if not block_types:
            continue
        for candidate_index in range(index - 1, -1, -1):
            candidate = messages[candidate_index]
            if candidate.role == "user" and candidate.metadata.get("isMeta"):
                uuid = str(candidate.metadata.get("uuid", ""))
                if uuid:
                    strip_targets.setdefault(uuid, set()).update(block_types)
                break
            if is_synthetic_api_error_message(candidate):
                continue
            break

    if not strip_targets:
        return list(messages)

    stripped_messages: list[Message] = []
    for message in messages:
        if message.role != "user" or not isinstance(message.content, list):
            stripped_messages.append(message)
            continue
        uuid = str(message.metadata.get("uuid", ""))
        block_types = strip_targets.get(uuid)
        if not block_types:
            stripped_messages.append(message)
            continue
        filtered_content = [
            block for block in message.content
            if not any(isinstance(block, block_type) for block_type in block_types)
        ]
        stripped_messages.append(
            Message(
                role=message.role,
                content=filtered_content,
                name=message.name,
                metadata=message.metadata,
            )
        )
    return stripped_messages


def normalize_messages_for_api(
    messages: list[Message],
    tools: list[Any] | None = None,
) -> list[Message]:
    """Prepare messages for the API call.

    Port of TS ``normalizeMessagesForAPI``:
    1. Filter out system messages (they go in the system param)
    2. Filter out virtual messages (display-only)
    3. Filter out synthetic API error messages
    4. Merge consecutive user messages
    5. Ensure every tool_use has a matching tool_result
    6. Ensure alternating user/assistant turns
    """
    available_tool_names: set[str] = set()
    if tools:
        for t in tools:
            name = getattr(t, "name", None) or getattr(t, "tool_name", "")
            if name:
                available_tool_names.add(name)

    # Phase 1: strip blocks that previously triggered synthetic API errors,
    # then filter non-API-visible messages.
    repaired_messages = _strip_problematic_blocks_after_api_errors(messages)

    filtered: list[Message] = []
    for msg in repaired_messages:
        # Skip system messages
        if msg.role == "system":
            continue
        # Skip virtual messages
        if msg.metadata.get("isVirtual"):
            continue
        # Skip synthetic API error messages
        if is_synthetic_api_error_message(msg):
            continue
        # Skip progress messages
        if msg.metadata.get("type") == "progress":
            continue
        filtered.append(msg)

    if not filtered:
        return []

    # Phase 2: drop whitespace-only assistant messages and ensure non-final
    # assistant messages never have empty content arrays.
    filtered = _filter_whitespace_only_assistant_messages(filtered)
    filtered = _ensure_non_empty_assistant_content(filtered)

    # Phase 3: merge consecutive user messages
    merged: list[Message] = []
    for msg in filtered:
        if merged and merged[-1].role == "user" and msg.role == "user":
            prev = merged[-1]
            prev_content = prev.content if isinstance(prev.content, list) else (
                [TextBlock(text=prev.content)] if prev.content else []
            )
            new_content = msg.content if isinstance(msg.content, list) else (
                [TextBlock(text=msg.content)] if msg.content else []
            )
            merged[-1] = Message(
                role="user",
                content=prev_content + new_content,
                metadata=_merge_user_message_metadata(prev.metadata, msg.metadata),
            )
        else:
            merged.append(msg)

    # Phase 4: ensure tool_use/tool_result pairing
    merged = _ensure_tool_result_pairing(merged)

    # Phase 5: ensure alternating turns (API requirement)
    # If first message is assistant, prepend empty user
    if merged and merged[0].role == "assistant":
        merged.insert(0, user_message(""))

    result: list[Message] = []
    for msg in merged:
        if result and result[-1].role == msg.role:
            if msg.role == "user":
                prev = result[-1]
                prev_content = prev.content if isinstance(prev.content, list) else (
                    [TextBlock(text=prev.content)] if prev.content else []
                )
                new_content = msg.content if isinstance(msg.content, list) else (
                    [TextBlock(text=msg.content)] if msg.content else []
                )
                result[-1] = Message(
                    role="user",
                    content=prev_content + new_content,
                    metadata=_merge_user_message_metadata(prev.metadata, msg.metadata),
                )
            elif msg.role == "assistant":
                prev = result[-1]
                prev_content = prev.content if isinstance(prev.content, list) else (
                    [TextBlock(text=prev.content)] if prev.content else []
                )
                new_content = msg.content if isinstance(msg.content, list) else (
                    [TextBlock(text=msg.content)] if msg.content else []
                )
                result[-1] = Message(
                    role="assistant",
                    content=prev_content + new_content,
                    metadata=prev.metadata,
                )
        else:
            result.append(msg)

    return result
