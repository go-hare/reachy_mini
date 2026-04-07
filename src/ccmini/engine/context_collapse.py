"""Context Collapse — read-time projection compression.

Ported from Claude Code's ``contextCollapse`` subsystem (codename: marble-origami):
- Unlike compact (which rewrites messages), this creates a **projected view**
  of older messages before sending to the API
- The original messages are preserved; only the API payload is modified
- Collapses are staged first, then committed when confirmed safe
- When enabled, it takes priority over auto-compact for proactive management
- Reactive compact remains as the 413-error fallback

Pipeline position: runs AFTER micro-compact but BEFORE auto-compact.

Collapse types:
1. **Tool result projection** — replace verbose tool results with one-line summaries
2. **Read-search group collapse** — merge consecutive read/search operations
3. **Thinking block removal** — strip thinking/reasoning blocks from old turns
4. **Attachment trimming** — remove large attachments from old messages
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..messages import (
    ContentBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    assistant_message,
    tool_result_content_snippet,
    tool_result_content_to_text,
    user_message,
)
from .compact import estimate_tokens, estimate_text_tokens

logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────

@dataclass(slots=True)
class CollapseConfig:
    """Configuration for context collapse.

    TS: the entire contextCollapse subsystem is gated behind
    ``feature('CONTEXT_COLLAPSE')`` which is compiled out in the
    recovered build.  ``enabled`` therefore defaults to ``False``
    so the no-op path is taken unless the caller opts in.
    """

    enabled: bool = False
    keep_recent_turns: int = 4
    tool_result_max_chars: int = 200
    collapse_thinking: bool = True
    collapse_attachments: bool = True
    collapse_read_search: bool = True
    min_message_age_turns: int = 3
    owns_context_management: bool = False


DEFAULT_COLLAPSE_CONFIG = CollapseConfig()


# ── Feature gate ────────────────────────────────────────────────────

def is_context_collapse_enabled(config: CollapseConfig | None = None) -> bool:
    """TS: ``contextCollapse?.isContextCollapseEnabled() ?? false``.

    The TS implementation checks an internal enabled flag that is only
    ``true`` when the ``CONTEXT_COLLAPSE`` feature gate is active AND
    the subsystem has been initialised.  We mirror that with
    ``config.enabled``.
    """
    if config is None:
        return False
    return config.enabled


# ── Collapse state ──────────────────────────────────────────────────

@dataclass
class CollapseEntry:
    """A single committed collapse."""
    message_index: int
    original_tokens: int
    collapsed_tokens: int
    collapse_type: str
    timestamp: float = 0.0


@dataclass
class CollapseState:
    """Tracks collapse activity."""

    committed: list[CollapseEntry] = field(default_factory=list)
    staged_count: int = 0
    total_tokens_saved: int = 0
    collapse_count: int = 0


_state = CollapseState()


def get_collapse_state() -> CollapseState:
    return _state


def reset_collapse_state() -> None:
    global _state
    _state = CollapseState()


# ── Core projection logic ──────────────────────────────────────────

def apply_collapses(
    messages: list[Message],
    config: CollapseConfig | None = None,
) -> tuple[list[Message], int]:
    """Apply read-time collapses to create a projected message view.

    Returns ``(projected_messages, tokens_saved)``. The original
    messages are NOT modified — this creates new Message objects
    for collapsed entries and reuses originals for untouched ones.

    This function is designed to run every turn, cheaply. No LLM calls.

    TS: ``contextCollapse.applyCollapsesIfNeeded(messagesForQuery, toolUseContext, querySource)``
    """
    if config is None:
        config = DEFAULT_COLLAPSE_CONFIG
    if not config.enabled or len(messages) <= config.keep_recent_turns * 2:
        return messages, 0

    # The recent window stays untouched
    boundary = len(messages) - (config.keep_recent_turns * 2)
    if boundary <= 0:
        return messages, 0

    projected: list[Message] = []
    total_saved = 0

    for i, msg in enumerate(messages):
        if i >= boundary:
            # Recent messages: keep as-is
            projected.append(msg)
            continue

        # Skip snip markers
        if msg.metadata.get("is_snip_marker"):
            projected.append(msg)
            continue

        collapsed_msg, saved = _collapse_message(msg, config)
        projected.append(collapsed_msg)
        total_saved += saved

    if total_saved > 0:
        _state.total_tokens_saved += total_saved
        _state.collapse_count += 1
        logger.debug("Context collapse saved ~%d tokens", total_saved)

    return projected, total_saved


def _collapse_message(
    msg: Message,
    config: CollapseConfig,
) -> tuple[Message, int]:
    """Collapse a single message. Returns (new_msg, tokens_saved)."""
    if isinstance(msg.content, str):
        return msg, 0

    original_tokens = _msg_tokens(msg)
    new_blocks: list[ContentBlock] = []
    modified = False

    for block in msg.content:
        collapsed = _collapse_block(block, msg.role, config)
        if collapsed is not block:
            modified = True
        new_blocks.append(collapsed)

    if not modified:
        return msg, 0

    new_msg = Message(
        role=msg.role,
        content=new_blocks,
        metadata={**msg.metadata, "_collapsed": True},
    )
    new_tokens = _msg_tokens(new_msg)
    saved = max(0, original_tokens - new_tokens)
    return new_msg, saved


def _collapse_block(
    block: ContentBlock,
    role: str,
    config: CollapseConfig,
) -> ContentBlock:
    """Collapse a single content block.

    TS collapses ``thinking`` and ``redacted_thinking`` content types.
    Python's ContentBlock union has no dedicated thinking type, so we
    check the block's ``type`` attribute (which every dataclass block
    carries) as well as a ``metadata`` dict for backwards compat.
    """

    # Collapse verbose tool results
    if isinstance(block, ToolResultBlock):
        rendered = tool_result_content_to_text(block.content)
        if len(rendered) > config.tool_result_max_chars:
            summary = tool_result_content_snippet(block.content, limit=80).strip()
            return ToolResultBlock(
                tool_use_id=block.tool_use_id,
                content=f"[Collapsed: {summary}...]",
                is_error=block.is_error,
            )

    # TS: skip thinking and redacted_thinking blocks
    if config.collapse_thinking and isinstance(block, TextBlock):
        block_type = getattr(block, "type", None)
        # Check the type attribute for thinking/redacted_thinking
        if block_type in ("thinking", "redacted_thinking"):
            return TextBlock(text="[thinking collapsed]")
        # Fallback: check metadata dict (older/test-only subclasses)
        block_metadata = getattr(block, "metadata", None)
        if isinstance(block_metadata, dict) and block_metadata.get("type") in (
            "thinking",
            "redacted_thinking",
        ):
            return TextBlock(text="[thinking collapsed]")

    # Collapse large text blocks in tool use inputs
    if isinstance(block, ToolUseBlock):
        input_str = str(block.input)
        if len(input_str) > config.tool_result_max_chars * 2:
            # Keep the tool name and a truncated input
            truncated_input: dict[str, Any] = {}
            if isinstance(block.input, dict):
                for k, v in block.input.items():
                    sv = str(v)
                    if len(sv) > 100:
                        truncated_input[k] = sv[:100] + "..."
                    else:
                        truncated_input[k] = v
            else:
                truncated_input = {"_collapsed": input_str[:200] + "..."}
            return ToolUseBlock(
                id=block.id,
                name=block.name,
                input=truncated_input,
            )

    return block


def _msg_tokens(msg: Message) -> int:
    """Estimate tokens for a single message."""
    if isinstance(msg.content, str):
        return len(msg.content) // 4

    total = 0
    for block in msg.content:
        if isinstance(block, TextBlock):
            total += len(block.text)
        elif isinstance(block, ToolUseBlock):
            total += len(block.name) + len(str(block.input))
        elif isinstance(block, ToolResultBlock):
            total += len(tool_result_content_to_text(block.content))
    return total // 4


# ── Overflow recovery ───────────────────────────────────────────────

def recover_from_overflow(
    messages: list[Message],
    config: CollapseConfig | None = None,
) -> tuple[list[Message], int]:
    """Aggressively collapse all collapsible content to recover from overflow.

    TS: ``contextCollapse.recoverFromOverflow(messagesForQuery, querySource)``
    — drains all staged collapses. If ``committed > 0`` the query retries
    with ``collapse_drain_retry``.

    This is the first line of defense when the API returns a 413 error.
    If this doesn't free enough, reactive_compact takes over.

    Uses a more aggressive config: keep fewer recent turns, lower char thresholds.
    """
    aggressive_config = CollapseConfig(
        enabled=True,
        keep_recent_turns=2,
        tool_result_max_chars=80,
        collapse_thinking=True,
        collapse_attachments=True,
        collapse_read_search=True,
        min_message_age_turns=1,
    )

    projected, saved = apply_collapses(messages, aggressive_config)
    if saved > 0:
        logger.info(
            "Overflow recovery via context collapse: ~%d tokens freed", saved,
        )
    return projected, saved


# ── Read-search group collapse ──────────────────────────────────────

# TS: collapseReadSearchGroups uses tool.isSearchOrReadCommand() to classify.
# These are the built-in tool names that the TS BashTool/ReadTool/GrepTool/
# GlobTool report as search or read via their isSearchOrReadCommand methods.
_READ_SEARCH_TOOLS = {"Read", "Glob", "Grep", "list_directory", "Bash"}


def collapse_read_search_groups(
    messages: list[Message],
    *,
    keep_recent: int = 4,
) -> tuple[list[Message], int]:
    """Collapse consecutive read/search tool calls into summary groups.

    TS: ``collapseReadSearchGroups(messages, tools)`` in collapseReadSearch.ts.

    The TS version uses ``getToolSearchOrReadInfo`` to classify each tool call
    via the tool's ``isSearchOrReadCommand`` method, then groups consecutive
    collapsible operations. Groups are broken by assistant text, non-collapsible
    tool uses, or user messages with non-collapsible tool results.

    This is a simplified version that operates on Message types for the
    query pipeline (the TS version works with RenderableMessage for UI).
    """
    if len(messages) <= keep_recent * 2:
        return messages, 0

    boundary = len(messages) - keep_recent * 2
    if boundary <= 0:
        return messages, 0

    result: list[Message] = []
    tokens_saved = 0
    i = 0

    while i < len(messages):
        if i >= boundary:
            result.append(messages[i])
            i += 1
            continue

        # Detect a run of read/search tool calls
        group_start = i
        group_tools: list[str] = []

        while i < boundary:
            msg = messages[i]
            if msg.role == "assistant" and not isinstance(msg.content, str):
                tool_names = [
                    b.name for b in msg.content
                    if isinstance(b, ToolUseBlock) and b.name in _READ_SEARCH_TOOLS
                ]
                if tool_names:
                    group_tools.extend(tool_names)
                    i += 1
                    # Skip the corresponding tool result
                    if i < boundary and messages[i].role == "user":
                        i += 1
                    continue
                # TS: isTextBreaker — assistant text with non-empty content breaks group
                has_text = any(
                    isinstance(b, TextBlock) and b.text.strip()
                    for b in msg.content
                    if isinstance(b, TextBlock)
                )
                if has_text:
                    break
                # TS: isNonCollapsibleToolUse — non-read/search tool use breaks group
                has_non_collapsible = any(
                    isinstance(b, ToolUseBlock) and b.name not in _READ_SEARCH_TOOLS
                    for b in msg.content
                )
                if has_non_collapsible:
                    break
            # TS: shouldSkipMessage — system/attachment messages don't break group
            # but user messages with non-collapsible results do break
            break

        if len(group_tools) >= 2:
            # Collapse the group
            unique_tools = list(dict.fromkeys(group_tools))
            summary = f"[Collapsed {len(group_tools)} operations: {', '.join(unique_tools)}]"
            original_tokens = estimate_tokens(messages[group_start:i])

            collapsed_assistant = assistant_message(summary)
            collapsed_user = user_message(f"[{len(group_tools)} tool results collapsed]")
            result.extend([collapsed_assistant, collapsed_user])

            new_tokens = estimate_tokens([collapsed_assistant, collapsed_user])
            tokens_saved += max(0, original_tokens - new_tokens)
        else:
            # Not enough to collapse, keep original
            for j in range(group_start, i):
                result.append(messages[j])
            if group_start == i:
                result.append(messages[i])
                i += 1

    return result, tokens_saved


__all__ = [
    "CollapseConfig",
    "CollapseEntry",
    "CollapseState",
    "DEFAULT_COLLAPSE_CONFIG",
    "apply_collapses",
    "collapse_read_search_groups",
    "get_collapse_state",
    "is_context_collapse_enabled",
    "recover_from_overflow",
    "reset_collapse_state",
]
