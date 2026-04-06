"""Brief mode — SendUserMessage pattern and chat/transcript view toggle.

Port of Claude Code's BriefTool + brief.ts command. In brief mode the
agent communicates via SendUserMessage tool calls rather than inline text.
This allows a clean "chat" view (only SendUserMessage output) vs a
"transcript" view (everything including tool calls).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .core import (
    feature,
    get_kairos_state,
    is_kairos_active,
    _mutate_state,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# View modes
# ---------------------------------------------------------------------------

class ViewMode(str, Enum):
    CHAT = "chat"             # Only show SendUserMessage content
    TRANSCRIPT = "transcript"  # Show everything (tool calls, thoughts, etc.)


# ---------------------------------------------------------------------------
# Brief state
# ---------------------------------------------------------------------------

@dataclass
class BriefState:
    enabled: bool = False
    view_mode: ViewMode = ViewMode.TRANSCRIPT
    user_msg_opt_in: bool = False
    message_count: int = 0


_brief_state = BriefState()


def is_brief_entitled() -> bool:
    """Is the user ALLOWED to use Brief? Combines feature flags + gate."""
    if not (feature("kairos") or feature("kairos_brief")):
        return False
    if is_kairos_active():
        return True
    import os
    if os.environ.get("MINI_BRIEF", "").lower() in ("1", "true"):
        return True
    return _brief_state.user_msg_opt_in


def is_brief_enabled() -> bool:
    """Is Brief actually active in the current session?"""
    if not (feature("kairos") or feature("kairos_brief")):
        return False
    if is_kairos_active():
        return True
    return _brief_state.user_msg_opt_in and is_brief_entitled()


def enable_brief() -> None:
    _brief_state.enabled = True
    _brief_state.user_msg_opt_in = True
    _brief_state.view_mode = ViewMode.CHAT
    _mutate_state(brief_enabled=True, user_msg_opt_in=True)
    logger.debug("Brief mode enabled")


def disable_brief() -> None:
    _brief_state.enabled = False
    _brief_state.user_msg_opt_in = False
    _brief_state.view_mode = ViewMode.TRANSCRIPT
    _mutate_state(brief_enabled=False, user_msg_opt_in=False)
    logger.debug("Brief mode disabled")


def toggle_brief() -> bool:
    """Toggle brief mode. Returns new state."""
    if is_brief_enabled():
        disable_brief()
        return False
    else:
        enable_brief()
        return True


def get_view_mode() -> ViewMode:
    return _brief_state.view_mode


def set_view_mode(mode: ViewMode) -> None:
    _brief_state.view_mode = mode


# ---------------------------------------------------------------------------
# SendUserMessage — the tool the agent uses to communicate in brief mode
# ---------------------------------------------------------------------------

@dataclass
class UserMessage:
    """A message from the agent to the user via SendUserMessage."""
    content: str
    title: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


_message_history: list[UserMessage] = []


def send_user_message(
    content: str,
    *,
    title: str = "",
    metadata: dict[str, Any] | None = None,
) -> UserMessage:
    """Record a SendUserMessage call from the agent.

    In brief/chat mode this is the primary way the agent communicates
    with the user. The UI filters to show only these messages in chat view.
    """
    msg = UserMessage(
        content=content,
        title=title,
        metadata=metadata or {},
    )
    _message_history.append(msg)
    _brief_state.message_count += 1
    logger.debug("SendUserMessage: %s", content[:80])
    return msg


def get_message_history(*, limit: int = 50) -> list[UserMessage]:
    return _message_history[-limit:]


def clear_message_history() -> None:
    _message_history.clear()
    _brief_state.message_count = 0


def render_send_user_message_payload(raw: str) -> str:
    """Convert a SendUserMessage tool payload/result into display text."""
    import json

    try:
        payload = json.loads(raw)
    except Exception:
        payload = {}

    if isinstance(payload, dict):
        title = str(payload.get("title", "")).strip()
        content = str(payload.get("content", "")).strip()
        if title and content:
            return f"{title}\n{content}"
        if content:
            return content
    return raw


def should_render_stream_event(
    event: Any,
    *,
    mode: ViewMode | None = None,
) -> bool:
    """Decide whether a stream event should be rendered in the current view."""
    current_mode = mode or get_view_mode()
    if current_mode == ViewMode.TRANSCRIPT:
        return True

    event_type = getattr(event, "type", "")
    tool_name = getattr(event, "tool_name", "")
    is_error = bool(getattr(event, "is_error", False))

    if event_type in {"error", "pending_tool_call"}:
        return True
    if event_type in {"text", "completion", "tool_progress", "tool_use_summary"}:
        return False
    if event_type == "tool_call":
        return False
    if event_type == "tool_result":
        return is_error or tool_name == "SendUserMessage"
    return True


# ---------------------------------------------------------------------------
# Message filtering for view modes
# ---------------------------------------------------------------------------

def filter_messages_for_view(
    messages: list[dict[str, Any]],
    mode: ViewMode | None = None,
) -> list[dict[str, Any]]:
    """Filter conversation messages based on the current view mode.

    In CHAT mode, only show:
    - User messages (from real user)
    - SendUserMessage tool results
    - Compact boundary markers

    In TRANSCRIPT mode, show everything.
    """
    if mode is None:
        mode = get_view_mode()

    if mode == ViewMode.TRANSCRIPT:
        return messages

    result: list[dict[str, Any]] = []
    for msg in messages:
        msg_type = msg.get("type") or msg.get("role", "")

        # Always show user messages
        if msg_type in ("user", "human"):
            if msg.get("metadata", {}).get("type") == "tick":
                continue
            result.append(msg)
            continue

        # Show compact boundaries
        if msg.get("metadata", {}).get("isCompactBoundary"):
            result.append(msg)
            continue

        # For assistant messages, only show SendUserMessage tool calls
        if msg_type in ("assistant",):
            content = msg.get("content", [])
            if isinstance(content, list):
                filtered_blocks = []
                for block in content:
                    if isinstance(block, dict):
                        tool_name = block.get("name", "")
                        if tool_name == "SendUserMessage":
                            filtered_blocks.append(block)
                        elif block.get("type") == "text":
                            pass  # skip inline text in brief mode
                if filtered_blocks:
                    result.append({**msg, "content": filtered_blocks})
            elif isinstance(content, str) and not content.strip():
                continue
            continue

    return result


# ---------------------------------------------------------------------------
# System prompt section for brief mode
# ---------------------------------------------------------------------------

BRIEF_PROACTIVE_SECTION = """\
## Communication

You MUST use the SendUserMessage tool to communicate with the user. \
Do not output text directly - all user-facing communication goes through \
SendUserMessage. This is how the chat view works: only SendUserMessage \
content appears in the user's chat stream.

Guidelines:
- Use SendUserMessage at checkpoints to mark where things stand.
- Keep messages concise and actionable.
- Include file paths and code snippets only when they're essential.
- For errors, summarize the issue and what you tried.
- For completions, state what was done and any follow-up items.
"""


def get_brief_system_prompt() -> str | None:
    """Return the brief-mode prompt section, or None if not active."""
    if not is_brief_enabled():
        return None
    return BRIEF_PROACTIVE_SECTION


# ---------------------------------------------------------------------------
# Brief context awareness — auto-summarize tool results, compress errors
# ---------------------------------------------------------------------------

@dataclass
class ConversationDensity:
    """Tracks tokens per turn for brief-mode context awareness."""
    total_tokens: int = 0
    total_turns: int = 0

    def record_turn(self, token_count: int) -> None:
        self.total_tokens += token_count
        self.total_turns += 1

    @property
    def tokens_per_turn(self) -> float:
        if self.total_turns == 0:
            return 0.0
        return self.total_tokens / self.total_turns

    def reset(self) -> None:
        self.total_tokens = 0
        self.total_turns = 0


_conversation_density = ConversationDensity()


def get_conversation_density() -> ConversationDensity:
    return _conversation_density


def summarize_tool_result(result: str, max_chars: int = 500) -> str:
    """Auto-summarize a tool result for brief mode.

    Truncates long outputs and appends a length note.
    """
    if not is_brief_enabled():
        return result
    if len(result) <= max_chars:
        return result
    truncated = result[:max_chars].rsplit("\n", 1)[0]
    omitted = len(result) - len(truncated)
    return f"{truncated}\n... ({omitted} chars omitted)"


def compress_error(error_text: str, max_chars: int = 300) -> str:
    """Compress an error message to its essential information.

    Strips stack frames and keeps the error type + message.
    """
    if not is_brief_enabled():
        return error_text
    if len(error_text) <= max_chars:
        return error_text

    lines = error_text.strip().splitlines()
    essential: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Traceback"):
            continue
        if stripped.startswith("File "):
            continue
        if stripped.startswith("at "):
            continue
        essential.append(stripped)
    compressed = "\n".join(essential)
    if len(compressed) > max_chars:
        compressed = compressed[:max_chars] + "..."
    return compressed


# ---------------------------------------------------------------------------
# Brief level — graduated response verbosity
# ---------------------------------------------------------------------------

class BriefLevel(str, Enum):
    NORMAL = "normal"       # Full responses
    BRIEF = "brief"         # Shortened responses
    MINIMAL = "minimal"     # One-line responses
    SILENT = "silent"       # Only errors and critical info


_brief_level = BriefLevel.NORMAL


def get_brief_level() -> BriefLevel:
    return _brief_level


def set_brief_level(level: BriefLevel) -> None:
    global _brief_level
    _brief_level = level
    logger.debug("Brief level set to: %s", level.value)
    if level == BriefLevel.NORMAL:
        if is_brief_enabled():
            disable_brief()
    elif level in (BriefLevel.BRIEF, BriefLevel.MINIMAL, BriefLevel.SILENT):
        if not is_brief_enabled():
            enable_brief()


def should_emit(level: BriefLevel = BriefLevel.NORMAL) -> bool:
    """Check if a message at *level* should be emitted given current brief level.

    Messages are emitted when the current brief level is less restrictive
    or equal to the required level.
    """
    order = [BriefLevel.NORMAL, BriefLevel.BRIEF, BriefLevel.MINIMAL, BriefLevel.SILENT]
    current_idx = order.index(_brief_level)
    required_idx = order.index(level)
    return required_idx >= current_idx


def format_for_brief_level(text: str) -> str:
    """Format text according to the current brief level."""
    if _brief_level == BriefLevel.NORMAL:
        return text
    if _brief_level == BriefLevel.BRIEF:
        lines = text.strip().splitlines()
        if len(lines) > 5:
            return "\n".join(lines[:5]) + f"\n... ({len(lines) - 5} more lines)"
        return text
    if _brief_level == BriefLevel.MINIMAL:
        first_line = text.strip().splitlines()[0] if text.strip() else ""
        if len(first_line) > 120:
            return first_line[:120] + "..."
        return first_line
    # SILENT — only return if it looks like an error
    lower = text.lower()
    if any(kw in lower for kw in ("error", "fail", "critical", "exception", "fatal")):
        return text.strip().splitlines()[0] if text.strip() else ""
    return ""
