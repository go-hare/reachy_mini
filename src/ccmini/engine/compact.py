"""Compaction pipeline aligned to Claude Code's compact/session-memory/reactive flow."""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass

from ..messages import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    assistant_message,
    system_message,
    tool_result_content_snippet,
    user_message,
)
from ..providers import BaseProvider

CHARS_PER_TOKEN = 4
CLEARED_TOOL_RESULT = "[Tool result cleared to save context]"
TIME_BASED_MC_CLEARED_MESSAGE = "[Old tool result content cleared]"
IMAGE_MAX_TOKEN_SIZE = 2000

# Token warning thresholds — aligned to TS autoCompact.ts
WARNING_THRESHOLD_BUFFER_TOKENS = 20_000
ERROR_THRESHOLD_BUFFER_TOKENS = 20_000
MANUAL_COMPACT_BUFFER_TOKENS = 3_000
AUTOCOMPACT_BUFFER_TOKENS = 13_000
MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
COMPACTABLE_TOOLS = frozenset(
    {
        "Read",
        "Bash",
        "shell",
        "Grep",
        "Glob",
        "WebSearch",
        "WebFetch",
        "Edit",
        "Write",
    }
)


@dataclass(slots=True)
class CompactionResult:
    boundary_marker: Message
    summary_messages: list[Message]
    attachments: list[Message]
    hook_results: list[Message]
    messages_to_keep: list[Message]
    pre_compact_token_count: int = 0
    post_compact_token_count: int = 0
    true_post_compact_token_count: int = 0


def build_post_compact_messages(result: CompactionResult) -> list[Message]:
    return [
        result.boundary_marker,
        *result.summary_messages,
        *result.messages_to_keep,
        *result.attachments,
        *result.hook_results,
    ]


def _calculate_tool_result_tokens(block: ToolResultBlock) -> int:
    """Calculate token count for a tool result block, handling string and array content."""
    if not block.content:
        return 0
    if isinstance(block.content, str):
        return estimate_text_tokens(block.content)
    # Array content (list of text/image/document blocks)
    if isinstance(block.content, list):
        total = 0
        for item in block.content:
            if isinstance(item, dict):
                item_type = item.get("type", "")
                if item_type == "text":
                    total += estimate_text_tokens(item.get("text", ""))
                elif item_type in ("image", "document"):
                    total += IMAGE_MAX_TOKEN_SIZE
            elif isinstance(item, TextBlock):
                total += estimate_text_tokens(item.text)
            else:
                total += IMAGE_MAX_TOKEN_SIZE
        return total
    return estimate_text_tokens(str(block.content))


def estimate_tokens(messages: list[Message]) -> int:
    """Estimate token count — port of TS ``tokenCountWithEstimation``.

    Walks backward from the end to find the last message with real usage
    data (``metadata.usage``).  If found, returns real usage + rough
    estimation for subsequent messages.  Falls back to pure estimation.
    """
    # Phase 1: walk backward to find last message with usage
    i = len(messages) - 1
    while i >= 0:
        msg = messages[i]
        usage = msg.metadata.get("usage") if msg.metadata else None
        if usage and isinstance(usage, dict):
            input_tokens = usage.get("input_tokens", 0) or 0
            output_tokens = usage.get("output_tokens", 0) or 0
            cache_creation = usage.get("cache_creation_input_tokens", 0) or 0
            cache_read = usage.get("cache_read_input_tokens", 0) or 0
            real_count = input_tokens + output_tokens + cache_creation + cache_read
            if real_count > 0:
                # Walk back past sibling records from same API response
                response_id = msg.metadata.get("response_id")
                if response_id:
                    j = i - 1
                    while j >= 0:
                        prior = messages[j]
                        prior_id = prior.metadata.get("response_id") if prior.metadata else None
                        if prior_id == response_id:
                            i = j
                        elif prior_id is not None:
                            break
                        j -= 1
                # real usage + rough estimate for messages after anchor
                return real_count + _rough_token_estimate(messages[i + 1:])
        i -= 1

    # Phase 2: no usage data — pure estimation
    return _rough_token_estimate(messages)


def _rough_token_estimate(messages: list[Message]) -> int:
    """Rough token estimation for a slice of messages (no real usage data)."""
    total = 0
    for message in messages:
        if message.role not in ("user", "assistant"):
            continue
        if isinstance(message.content, str):
            total += len(message.content) // CHARS_PER_TOKEN
            continue
        for block in message.content:
            if isinstance(block, TextBlock):
                total += estimate_text_tokens(block.text)
            elif isinstance(block, ToolUseBlock):
                total += estimate_text_tokens(
                    block.name + json.dumps(block.input or {}, separators=(",", ":"))
                )
            elif isinstance(block, ToolResultBlock):
                total += _calculate_tool_result_tokens(block)
            else:
                # image, document, or other block types
                total += IMAGE_MAX_TOKEN_SIZE
    # Pad estimate by 4/3 to be conservative since we're approximating
    return math.ceil(total * (4 / 3))


def estimate_text_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def _format_compact_summary(summary: str) -> str:
    formatted = re.sub(r"<analysis>[\s\S]*?</analysis>", "", summary)
    summary_match = re.search(r"<summary>([\s\S]*?)</summary>", formatted)
    if summary_match:
        content = (summary_match.group(1) or "").strip()
        formatted = re.sub(
            r"<summary>[\s\S]*?</summary>",
            f"Summary:\n{content}",
            formatted,
        )
    return re.sub(r"\n\n+", "\n\n", formatted).strip()


def _get_compact_user_summary_message(
    summary: str,
    *,
    suppress_follow_up_questions: bool,
    transcript_path: str,
    recent_messages_preserved: bool = False,
) -> str:
    formatted_summary = _format_compact_summary(summary)
    base = (
        "This session is being continued from a previous conversation that ran out of context. "
        "The summary below covers the earlier portion of the conversation.\n\n"
        f"{formatted_summary}"
    )
    if transcript_path:
        base += (
            "\n\nIf you need specific details from before compaction "
            "(like exact code snippets, error messages, or content you generated), "
            f"read the full transcript at: {transcript_path}"
        )
    if recent_messages_preserved:
        base += "\n\nRecent messages are preserved verbatim."
    if suppress_follow_up_questions:
        base += (
            "\nContinue the conversation from where it left off without asking the user any further questions. "
            "Resume directly \u2014 do not acknowledge the summary, do not recap what was happening, "
            "do not preface with \u2018I\u2019ll continue\u2019 or similar. Pick up the last task as if the break never happened."
        )
    return base


def _create_compact_boundary_message(
    trigger: str,
    pre_tokens: int,
    last_pre_compact_message_uuid: str = "",
) -> Message:
    metadata = {
        "isCompactBoundary": True,
        "subtype": "compact_boundary",
        "level": "info",
        "isMeta": False,
        "compactMetadata": {
            "trigger": trigger,
            "preTokens": pre_tokens,
        },
    }
    if last_pre_compact_message_uuid:
        metadata["logicalParentUuid"] = last_pre_compact_message_uuid
    return system_message("Conversation compacted", **metadata)


def _annotate_boundary_with_preserved_segment(
    boundary: Message,
    anchor_uuid: str,
    messages_to_keep: list[Message] | None,
) -> Message:
    keep = messages_to_keep or []
    if not keep:
        return boundary
    metadata = dict(boundary.metadata)
    compact_metadata = dict(metadata.get("compactMetadata", {}))
    compact_metadata["preservedSegment"] = {
        "headUuid": str(keep[0].metadata.get("uuid", "")),
        "anchorUuid": anchor_uuid,
        "tailUuid": str(keep[-1].metadata.get("uuid", "")),
    }
    metadata["compactMetadata"] = compact_metadata
    return Message(
        role=boundary.role,
        content=boundary.content,
        name=boundary.name,
        metadata=metadata,
    )


@dataclass(slots=True)
class SessionMemoryCompactConfig:
    min_tokens: int = 10_000
    min_text_block_messages: int = 5
    max_tokens: int = 40_000


DEFAULT_SM_COMPACT_CONFIG = SessionMemoryCompactConfig()


def _has_text_blocks(message: Message) -> bool:
    """Check if a message contains text blocks (matches TS hasTextBlocks)."""
    if message.role == "assistant":
        if isinstance(message.content, str):
            return False
        return any(isinstance(block, TextBlock) for block in message.content)
    if message.role == "user":
        if isinstance(message.content, str):
            return len(message.content) > 0
        return any(isinstance(block, TextBlock) for block in message.content)
    return False


def _get_tool_result_ids(message: Message) -> list[str]:
    if message.role != "user" or isinstance(message.content, str):
        return []
    return [block.tool_use_id for block in message.content if isinstance(block, ToolResultBlock)]


def _has_tool_use_with_ids(message: Message, tool_use_ids: set[str]) -> bool:
    if message.role != "assistant" or isinstance(message.content, str):
        return False
    return any(isinstance(block, ToolUseBlock) and block.id in tool_use_ids for block in message.content)


def _adjust_index_to_preserve_api_invariants(messages: list[Message], start_index: int) -> int:
    if start_index <= 0 or start_index >= len(messages):
        return start_index

    adjusted_index = start_index
    all_tool_result_ids: list[str] = []
    for idx in range(start_index, len(messages)):
        all_tool_result_ids.extend(_get_tool_result_ids(messages[idx]))

    if all_tool_result_ids:
        tool_use_ids_in_kept_range: set[str] = set()
        for idx in range(adjusted_index, len(messages)):
            message = messages[idx]
            if message.role != "assistant" or isinstance(message.content, str):
                continue
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    tool_use_ids_in_kept_range.add(block.id)
        needed_tool_use_ids = {
            tool_use_id
            for tool_use_id in all_tool_result_ids
            if tool_use_id not in tool_use_ids_in_kept_range
        }
        for idx in range(adjusted_index - 1, -1, -1):
            if not needed_tool_use_ids:
                break
            message = messages[idx]
            if _has_tool_use_with_ids(message, needed_tool_use_ids):
                adjusted_index = idx
                if not isinstance(message.content, str):
                    for block in message.content:
                        if isinstance(block, ToolUseBlock):
                            needed_tool_use_ids.discard(block.id)

    kept_assistant_ids = {
        str(messages[idx].metadata.get("assistantId", "")).strip()
        for idx in range(adjusted_index, len(messages))
        if messages[idx].role == "assistant"
    }
    kept_assistant_ids.discard("")
    for idx in range(adjusted_index - 1, -1, -1):
        message = messages[idx]
        assistant_id = str(message.metadata.get("assistantId", "")).strip()
        if message.role == "assistant" and assistant_id and assistant_id in kept_assistant_ids:
            adjusted_index = idx

    return adjusted_index


def _calculate_messages_to_keep_index(messages: list[Message], last_summarized_index: int) -> int:
    if not messages:
        return 0
    config = DEFAULT_SM_COMPACT_CONFIG
    start_index = last_summarized_index + 1 if last_summarized_index >= 0 else len(messages)
    total_tokens = 0
    text_block_message_count = 0
    for idx in range(start_index, len(messages)):
        msg = messages[idx]
        total_tokens += estimate_tokens([msg])
        if _has_text_blocks(msg):
            text_block_message_count += 1
    if total_tokens >= config.max_tokens:
        return _adjust_index_to_preserve_api_invariants(messages, start_index)
    if total_tokens >= config.min_tokens and text_block_message_count >= config.min_text_block_messages:
        return _adjust_index_to_preserve_api_invariants(messages, start_index)

    from ..messages import is_compact_boundary_message

    idx = -1
    for offset, msg in enumerate(messages):
        if is_compact_boundary_message(msg):
            idx = offset
    floor = 0 if idx == -1 else idx + 1
    for idx in range(start_index - 1, floor - 1, -1):
        msg = messages[idx]
        total_tokens += estimate_tokens([msg])
        if _has_text_blocks(msg):
            text_block_message_count += 1
        start_index = idx
        if total_tokens >= config.max_tokens:
            break
        if total_tokens >= config.min_tokens and text_block_message_count >= config.min_text_block_messages:
            break

    return _adjust_index_to_preserve_api_invariants(messages, start_index)


@dataclass(slots=True)
class CompactConfig:
    context_window: int = 200_000
    max_output_tokens_for_summary: int = 20_000
    max_output_tokens_for_model: int = 20_000  # model-specific max output tokens
    auto_compact_buffer: int = 13_000
    keep_recent: int = 5
    enabled: bool = True
    micro_compact_enabled: bool = True
    micro_compact_keep_recent: int = 5
    time_based_gap_minutes: int = 60
    max_consecutive_failures: int = 3

    @property
    def effective_window(self) -> int:
        # TS: Math.min(getMaxOutputTokensForModel(model), MAX_OUTPUT_TOKENS_FOR_SUMMARY)
        reserved = min(self.max_output_tokens_for_model, self.max_output_tokens_for_summary)
        context_window = self.context_window
        # Allow overriding the context window for testing
        auto_compact_window = os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "")
        if auto_compact_window:
            try:
                parsed = int(auto_compact_window)
                if parsed > 0:
                    context_window = min(context_window, parsed)
            except ValueError:
                pass
        return context_window - reserved

    @property
    def auto_compact_threshold(self) -> int:
        base = self.effective_window - self.auto_compact_buffer
        # Override for easier testing of autocompact
        env_pct = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "")
        if env_pct:
            try:
                parsed = float(env_pct)
                if 0 < parsed <= 100:
                    pct_threshold = int(self.effective_window * (parsed / 100))
                    return min(pct_threshold, base)
            except ValueError:
                pass
        return base


DEFAULT_CONFIG = CompactConfig()


@dataclass(frozen=True, slots=True)
class TokenWarningState:
    """Mirrors TS calculateTokenWarningState return value."""
    percent_left: int
    is_above_warning_threshold: bool
    is_above_error_threshold: bool
    is_above_auto_compact_threshold: bool
    is_at_blocking_limit: bool


PROMPT_TOO_LONG_ERROR_MESSAGE = (
    "The conversation is too long for the model's context window. "
    "Please use /compact to reduce the conversation size."
)


def calculate_token_warning_state(
    token_usage: int,
    config: CompactConfig = DEFAULT_CONFIG,
) -> TokenWarningState:
    """Calculate token warning/blocking thresholds — 1:1 port of TS calculateTokenWarningState."""
    threshold = config.auto_compact_threshold if config.enabled else config.effective_window

    percent_left = max(0, round(((threshold - token_usage) / threshold) * 100)) if threshold > 0 else 0

    warning_threshold = threshold - WARNING_THRESHOLD_BUFFER_TOKENS
    error_threshold = threshold - ERROR_THRESHOLD_BUFFER_TOKENS

    is_above_warning = token_usage >= warning_threshold
    is_above_error = token_usage >= error_threshold

    is_above_auto_compact = config.enabled and token_usage >= config.auto_compact_threshold

    # Blocking limit: actual context window minus a small buffer
    actual_context_window = config.effective_window
    default_blocking_limit = actual_context_window - MANUAL_COMPACT_BUFFER_TOKENS

    # Allow override for testing
    blocking_override = os.environ.get("CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE", "")
    if blocking_override:
        try:
            parsed = int(blocking_override)
            if parsed > 0:
                blocking_limit = parsed
            else:
                blocking_limit = default_blocking_limit
        except ValueError:
            blocking_limit = default_blocking_limit
    else:
        blocking_limit = default_blocking_limit

    is_at_blocking = token_usage >= blocking_limit

    return TokenWarningState(
        percent_left=percent_left,
        is_above_warning_threshold=is_above_warning,
        is_above_error_threshold=is_above_error,
        is_above_auto_compact_threshold=is_above_auto_compact,
        is_at_blocking_limit=is_at_blocking,
    )


@dataclass(slots=True)
class CompactTracker:
    compacted: bool = False
    turn_counter: int = 0
    turn_id: str = ""
    consecutive_failures: int = 0
    last_compact_time: float = 0.0
    tokens_freed: int = 0

    @property
    def circuit_broken(self) -> bool:
        return self.consecutive_failures >= DEFAULT_CONFIG.max_consecutive_failures

    def record_success(self, tokens_freed: int = 0) -> None:
        self.compacted = True
        self.turn_counter = 0
        self.consecutive_failures = 0
        self.last_compact_time = time.monotonic()
        self.tokens_freed += tokens_freed

    def record_failure(self) -> None:
        self.consecutive_failures += 1


def is_auto_compact_enabled() -> bool:
    if os.environ.get("DISABLE_COMPACT", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    if os.environ.get("DISABLE_AUTO_COMPACT", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    return True


def _collect_compactable_tool_ids(messages: list[Message]) -> list[str]:
    """Walk messages and collect tool_use IDs whose tool name is in COMPACTABLE_TOOLS, in encounter order."""
    ids: list[str] = []
    for message in messages:
        if message.role == "assistant" and not isinstance(message.content, str):
            for block in message.content:
                if isinstance(block, ToolUseBlock) and block.name in COMPACTABLE_TOOLS:
                    ids.append(block.id)
    return ids


def micro_compact(
    messages: list[Message],
    *,
    config: CompactConfig = DEFAULT_CONFIG,
    query_source: str = "",
) -> tuple[list[Message], int]:
    del query_source
    if not config.micro_compact_enabled:
        return messages, 0

    compactable_ids = _collect_compactable_tool_ids(messages)
    if len(compactable_ids) <= config.micro_compact_keep_recent:
        return messages, 0

    # Floor at 1: slice(-0) returns the full array
    keep_recent = max(1, config.micro_compact_keep_recent)
    keep_set = set(compactable_ids[-keep_recent:])
    clear_set = set(id_ for id_ in compactable_ids if id_ not in keep_set)

    if not clear_set:
        return messages, 0

    new_messages = list(messages)
    tokens_saved = 0
    for message_index, message in enumerate(new_messages):
        if message.role != "user" or isinstance(message.content, str):
            continue
        touched = False
        new_content = list(message.content)
        for block_index, block in enumerate(new_content):
            if not isinstance(block, ToolResultBlock):
                continue
            if block.tool_use_id not in clear_set:
                continue
            if block.content == CLEARED_TOOL_RESULT:
                continue
            if block.content == TIME_BASED_MC_CLEARED_MESSAGE:
                continue
            old_tokens = _calculate_tool_result_tokens(block)
            new_content[block_index] = ToolResultBlock(
                tool_use_id=block.tool_use_id,
                content=CLEARED_TOOL_RESULT,
                is_error=block.is_error,
            )
            tokens_saved += old_tokens - estimate_text_tokens(CLEARED_TOOL_RESULT)
            touched = True
        if touched:
            new_messages[message_index] = Message(
                role=message.role,
                content=new_content,
                name=message.name,
                metadata=message.metadata,
            )

    return new_messages, tokens_saved


def collapse_tool_sequences(messages: list[Message], *, keep_recent: int = 5) -> list[Message]:
    if len(messages) <= keep_recent:
        return list(messages)

    boundary = len(messages) - keep_recent
    head = messages[:boundary]
    tail = messages[boundary:]
    result: list[Message] = []
    index = 0
    while index < len(head):
        message = head[index]
        if message.role == "assistant" and message.has_tool_use:
            tool_names = [block.name for block in message.tool_use_blocks]
            summaries: list[str] = []
            next_index = index + 1
            if next_index < len(head) and head[next_index].role == "user":
                result_message = head[next_index]
                for tool_result in result_message.tool_result_blocks:
                    snippet = tool_result_content_snippet(tool_result.content, limit=120)
                    status = "error" if tool_result.is_error else "ok"
                    summaries.append(f"{status}: {snippet}")
                next_index += 1
            tool_list = ", ".join(tool_names)
            summary_text = "; ".join(summaries) if summaries else "completed"
            result.append(assistant_message(f"[Collapsed tool calls: {tool_list} -> {summary_text}]"))
            index = next_index
        else:
            result.append(message)
            index += 1
    result.extend(tail)
    return result


COMPACT_SYSTEM_PROMPT = """\
You are a conversation summariser for an AI coding agent.

RULES:
- Preserve all important technical details, paths, symbols, errors, and decisions.
- Preserve current task state: what is done, what remains, and what the user corrected.
- Omit pleasantries and redundant chatter.
- Be concise but complete.
"""


def strip_images_from_messages(messages: list[Message]) -> list[Message]:
    """Strip image/document blocks from user messages before compaction.

    Replaces image blocks with a text marker so the summary still notes
    that an image was shared. Only user messages contain images.
    Also strips images/documents nested inside tool_result content arrays.
    """
    result: list[Message] = []
    for message in messages:
        if message.role != "user":
            result.append(message)
            continue
        if isinstance(message.content, str):
            result.append(message)
            continue
        has_media = False
        new_content: list = []
        for block in message.content:
            if isinstance(block, TextBlock):
                new_content.append(block)
            elif isinstance(block, ToolResultBlock):
                # Check for nested images/documents inside tool_result content
                if isinstance(block.content, list):
                    tool_has_media = False
                    new_tool_content = []
                    for item in block.content:
                        if isinstance(item, dict) and item.get("type") in ("image", "document"):
                            tool_has_media = True
                            new_tool_content.append({"type": "text", "text": f"[{item.get('type', 'image')}]"})
                        else:
                            new_tool_content.append(item)
                    if tool_has_media:
                        has_media = True
                        new_content.append(
                            ToolResultBlock(
                                tool_use_id=block.tool_use_id,
                                content=new_tool_content,
                                is_error=block.is_error,
                            )
                        )
                    else:
                        new_content.append(block)
                else:
                    new_content.append(block)
            elif isinstance(block, ToolUseBlock):
                new_content.append(block)
            else:
                # image, document, or other media block
                has_media = True
                new_content.append(TextBlock(text="[image]"))
        if not has_media:
            result.append(message)
        else:
            result.append(
                Message(
                    role=message.role,
                    content=new_content,
                    name=message.name,
                    metadata=message.metadata,
                )
            )
    return result


async def _summarise(messages: list[Message], provider: BaseProvider, *, max_tokens: int = 4096) -> str:
    conversation_text = _render_messages_for_summary(strip_images_from_messages(messages))
    prompt = (
        "Summarise the following conversation between a user and an AI coding assistant. "
        "Keep all important technical details.\n\n"
        f"{conversation_text}"
    )
    response = await provider.complete(
        messages=[user_message(prompt)],
        system=COMPACT_SYSTEM_PROMPT,
        max_tokens=max_tokens,
        temperature=0.0,
        query_source="compact",
    )
    return response.text


def _render_messages_for_summary(messages: list[Message]) -> str:
    parts: list[str] = []
    for message in messages:
        text = message.text.strip()
        if text:
            parts.append(f"[{message.role.upper()}]: {text[:2000]}")
    return "\n\n".join(parts)


def should_auto_compact(
    messages: list[Message],
    config: CompactConfig = DEFAULT_CONFIG,
    *,
    tracker: CompactTracker | None = None,
    extra_tokens_freed: int = 0,
    query_source: str = "",
) -> bool:
    if not is_auto_compact_enabled():
        return False
    if query_source in {"compact", "session_memory", "marble_origami"}:
        return False
    return estimate_tokens(messages) - extra_tokens_freed >= config.auto_compact_threshold


def _create_summary_message(
    summary_text: str,
    *,
    mode: str,
    transcript_path: str,
    recent_messages_preserved: bool = False,
) -> Message:
    return user_message(
        _get_compact_user_summary_message(
            summary_text,
            suppress_follow_up_questions=True,
            transcript_path=transcript_path,
            recent_messages_preserved=recent_messages_preserved,
        ),
        isCompactSummary=True,
        isVisibleInTranscriptOnly=True,
    )


def _create_compaction_result(
    *,
    mode: str,
    original_messages: list[Message],
    summary_message: Message,
    messages_to_keep: list[Message],
) -> CompactionResult:
    boundary = _annotate_boundary_with_preserved_segment(
        _create_compact_boundary_message(
            mode,
            estimate_tokens(original_messages),
            str(original_messages[-1].metadata.get("uuid", "")) if original_messages else "",
        ),
        str(summary_message.metadata.get("uuid", "")),
        messages_to_keep,
    )
    return CompactionResult(
        boundary_marker=boundary,
        summary_messages=[summary_message],
        attachments=[],
        hook_results=[],
        messages_to_keep=messages_to_keep,
        pre_compact_token_count=estimate_tokens(original_messages),
        post_compact_token_count=estimate_tokens([summary_message]),
        true_post_compact_token_count=estimate_tokens(
            [boundary, summary_message, *messages_to_keep]
        ),
    )


async def auto_compact_if_needed(
    messages: list[Message],
    provider: BaseProvider,
    *,
    config: CompactConfig = DEFAULT_CONFIG,
    tracker: CompactTracker | None = None,
    session_memory_content: str | None = None,
    query_source: str = "",
    extra_tokens_freed: int = 0,
    transcript_path: str = "",
    conversation_id: str = "",
) -> tuple[CompactionResult | None, bool]:
    # Early exit if compact is globally disabled (matches TS autoCompactIfNeeded)
    if os.environ.get("DISABLE_COMPACT", "").strip().lower() in {"1", "true", "yes", "on"}:
        return None, False

    # Circuit breaker: stop retrying after N consecutive failures
    if tracker and tracker.circuit_broken:
        return None, False

    if not should_auto_compact(
        messages,
        config,
        tracker=tracker,
        extra_tokens_freed=extra_tokens_freed,
        query_source=query_source,
    ):
        return None, False

    try:
        original_messages = list(messages)
        messages, _ = micro_compact(messages, config=config, query_source=query_source)

        if session_memory_content and session_memory_content.strip():
            session_result = _compact_with_session_memory(
                messages,
                session_memory_content,
                config=config,
                transcript_path=transcript_path,
                conversation_id=conversation_id,
            )
            if session_result is not None:
                if tracker:
                    tracker.record_success(
                        estimate_tokens(original_messages) - estimate_tokens(build_post_compact_messages(session_result))
                    )
                return session_result, True

        messages = collapse_tool_sequences(messages, keep_recent=config.keep_recent)
        if len(messages) <= config.keep_recent + 1:
            return None, False

        to_summarise = messages[:-config.keep_recent]
        to_keep = messages[-config.keep_recent:]
        summary = await _summarise(to_summarise, provider)
        result = _create_compaction_result(
            mode="auto",
            original_messages=original_messages,
            summary_message=_create_summary_message(
                summary,
                mode="auto",
                transcript_path=transcript_path,
            ),
            messages_to_keep=to_keep,
        )
        if tracker:
            tracker.record_success(
                estimate_tokens(original_messages) - estimate_tokens(build_post_compact_messages(result))
            )
        return result, True
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        # Re-raise prompt-too-long errors — they need to propagate (matches TS)
        if str(exc) == PROMPT_TOO_LONG_ERROR_MESSAGE:
            raise
        if tracker:
            tracker.record_failure()
        return None, False


def _compact_with_session_memory(
    messages: list[Message],
    session_memory: str,
    *,
    config: CompactConfig = DEFAULT_CONFIG,
    transcript_path: str = "",
    conversation_id: str = "",
) -> CompactionResult | None:
    from ..messages import is_compact_boundary_message
    from ..services.session_memory import get_session_memory_state, is_session_memory_empty, truncate_for_compact

    if not session_memory.strip() or is_session_memory_empty(session_memory):
        return None
    if len(messages) <= 1:
        return None

    state = get_session_memory_state(conversation_id if conversation_id else None)
    last_uuid = str(state.last_memory_message_uuid or "").strip()
    if last_uuid:
        last_summarized_index = next(
            (idx for idx, message in enumerate(messages) if str(message.metadata.get("uuid", "")).strip() == last_uuid),
            -1,
        )
        if last_summarized_index == -1:
            return None
    else:
        last_summarized_index = len(messages) - 1

    start_index = _calculate_messages_to_keep_index(messages, last_summarized_index)
    messages_to_keep = [message for message in messages[start_index:] if not is_compact_boundary_message(message)]

    compact_text = truncate_for_compact(session_memory, max_tokens=max(1000, config.effective_window // 8))
    summary_message = _create_summary_message(
        compact_text,
        mode="session_memory",
        transcript_path=transcript_path,
        recent_messages_preserved=True,
    )
    result = _create_compaction_result(
        mode="session_memory",
        original_messages=messages,
        summary_message=summary_message,
        messages_to_keep=messages_to_keep,
    )
    if estimate_tokens(build_post_compact_messages(result)) >= config.auto_compact_threshold:
        compact_text = truncate_for_compact(session_memory, max_tokens=max(500, config.effective_window // 12))
        result = _create_compaction_result(
            mode="session_memory",
            original_messages=messages,
            summary_message=_create_summary_message(
                compact_text,
                mode="session_memory",
                transcript_path=transcript_path,
                recent_messages_preserved=True,
            ),
            messages_to_keep=messages_to_keep,
        )
    return result


async def reactive_compact(
    messages: list[Message],
    provider: BaseProvider,
    *,
    keep_recent: int = 5,
    token_gap: int | None = None,
    query_source: str = "",
    transcript_path: str = "",
) -> CompactionResult:
    del token_gap
    original_messages = list(messages)
    messages, _ = micro_compact(messages, query_source=query_source)
    messages = collapse_tool_sequences(messages, keep_recent=keep_recent)
    if len(messages) <= keep_recent + 1:
        return CompactionResult(
            boundary_marker=_create_compact_boundary_message(
                "auto",
                estimate_tokens(original_messages),
                str(original_messages[-1].metadata.get("uuid", "")) if original_messages else "",
            ),
            summary_messages=[],
            attachments=[],
            hook_results=[],
            messages_to_keep=messages,
            pre_compact_token_count=estimate_tokens(original_messages),
            post_compact_token_count=estimate_tokens(messages),
            true_post_compact_token_count=estimate_tokens(messages),
        )

    to_summarise = messages[:-keep_recent]
    to_keep = messages[-keep_recent:]
    summary = await _summarise(to_summarise, provider)
    return _create_compaction_result(
        mode="reactive",
        original_messages=original_messages,
        summary_message=_create_summary_message(
            summary,
            mode="reactive",
            transcript_path=transcript_path,
        ),
        messages_to_keep=to_keep,
    )
