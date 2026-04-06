"""Session Memory — automatic conversation notes maintained by a forked agent.

Ported from Claude Code's SessionMemory subsystem:
- A background agent periodically updates a structured markdown file
  with notes about the current conversation
- Triggered by thresholds: token count + tool calls since last update
- The forked agent can ONLY edit the session memory file
- Integrates with auto-compact: session memory serves as an efficient
  conversation summary
- **Per-conversation state** (``_states[conversation_id]``): compact cursor
  (``last_memory_message_uuid``), extraction flags, and in-memory note cache.
  Thresholds share ``_shared_memory_config`` (Hook / remote JSON).

The markdown template has structured sections for:
- Current state, task specification, files, workflow, errors,
  codebase docs, learnings, key results, worklog
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..hooks import PostSamplingHook
from ..messages import Message, ToolCallEvent
from ..paths import mini_agent_path
from ..tool import Tool, ToolUseContext

if TYPE_CHECKING:
    from ..providers import BaseProvider
    from ..hooks import PostSamplingContext

logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────

@dataclass(slots=True)
class SessionMemoryConfig:
    """Thresholds controlling when session memory updates trigger."""

    min_tokens_to_init: int = 10_000
    min_tokens_between_updates: int = 5_000
    tool_calls_between_updates: int = 3
    max_section_tokens: int = 2_000
    max_total_tokens: int = 12_000


DEFAULT_CONFIG = SessionMemoryConfig()


# ── Template ────────────────────────────────────────────────────────

DEFAULT_TEMPLATE = """\
# Session Title
_A short and distinctive 5-10 word descriptive title for the session_

# Current State
_What is actively being worked on right now? Pending tasks not yet completed. Immediate next steps._

# Task Specification
_What did the user ask to build? Any design decisions or other explanatory context_

# Files and Functions
_What are the important files? In short, what do they contain and why are they relevant?_

# Workflow
_What bash commands are usually run and in what order? How to interpret their output?_

# Errors & Corrections
_Errors encountered and how they were fixed. What did the user correct?_

# Codebase and System Documentation
_What are the important system components? How do they work/fit together?_

# Learnings
_What has worked well? What has not? What to avoid?_

# Key Results
_If the user asked for a specific output such as an answer, table, or document, repeat it here_

# Worklog
_Step by step, what was attempted and done? Very terse summary for each step_
"""


UPDATE_PROMPT_TEMPLATE = """\
IMPORTANT: This message is NOT part of the user conversation. Do NOT \
reference note-taking in the notes content.

Based on the conversation above, update the session notes file.

The file {notes_path} has current contents:
<current_notes>
{current_notes}
</current_notes>

Your ONLY task: use the edit tool to update the notes, then stop.

RULES:
- NEVER modify section headers (# lines) or italic descriptions (_ lines)
- ONLY update content BELOW each section's italic description
- Write DETAILED, INFO-DENSE content: file paths, function names, commands
- Keep each section under ~{max_section_tokens} tokens
- Always update "Current State" to reflect the most recent work
- Skip sections with no new insights (don't add filler)
- For "Key Results", include complete exact output the user requested
- Do not reference these instructions in the notes
{section_warnings}
"""


# ── State tracking ──────────────────────────────────────────────────

@dataclass
class SessionMemoryState:
    """Mutable state for session memory tracking."""

    initialized: bool = False
    tokens_at_last_extraction: int = 0
    last_memory_message_uuid: str = ""
    extraction_in_progress: bool = False
    extraction_started_at: float = 0.0
    config: SessionMemoryConfig = field(default_factory=lambda: SessionMemoryConfig())
    memory_path: str = ""
    current_content: str = ""

    def mark_extraction_started(self) -> None:
        self.extraction_in_progress = True
        self.extraction_started_at = time.monotonic()

    def mark_extraction_completed(self) -> None:
        self.extraction_in_progress = False
        self.extraction_started_at = 0.0

    def record_extraction(self, token_count: int, last_uuid: str = "") -> None:
        self.tokens_at_last_extraction = token_count
        if last_uuid:
            self.last_memory_message_uuid = last_uuid


# Shared thresholds (SessionMemoryHook / remote config); per-session everything else.
_shared_memory_config = SessionMemoryConfig()
_states: dict[str, SessionMemoryState] = {}


def _session_key(conversation_id: str | None) -> str:
    return conversation_id if conversation_id else ""


def _get_state(conversation_id: str | None = None) -> SessionMemoryState:
    """Return isolated state for this session (key ``conversation_id`` or ``\"\"``)."""
    key = _session_key(conversation_id)
    if key not in _states:
        st = SessionMemoryState()
        st.config = _shared_memory_config
        _states[key] = st
    return _states[key]


def get_session_memory_state(conversation_id: str | None = None) -> SessionMemoryState:
    """Per-session state: tokens, last_memory_message_uuid (compact cursor), cache paths."""
    return _get_state(conversation_id)


def reset_session_memory_state(conversation_id: str | None = None) -> None:
    """Clear one session's state, or all sessions if *conversation_id* is omitted."""
    global _states, _shared_memory_config
    if conversation_id is None:
        _states.clear()
        _shared_memory_config = SessionMemoryConfig()
        return
    _states.pop(_session_key(conversation_id), None)


# ── Token estimation (inline to avoid circular imports) ─────────────

def _estimate_tokens(messages: list[Message]) -> int:
    total = 0
    for msg in messages:
        if isinstance(msg.content, str):
            total += len(msg.content)
        else:
            for block in msg.content:
                total += len(str(getattr(block, "text", "") or getattr(block, "content", "") or ""))
    return total // 4


# ── Threshold checks ───────────────────────────────────────────────

def _count_tool_calls_since(
    messages: list[Message],
    since_uuid: str,
) -> int:
    """Count tool_use blocks in assistant messages after the given UUID."""
    tool_count = 0
    found_start = not since_uuid

    for msg in messages:
        if not found_start:
            uuid = msg.metadata.get("uuid", "")
            if uuid == since_uuid:
                found_start = True
            continue

        if msg.role == "assistant" and not isinstance(msg.content, str):
            for block in msg.content:
                if hasattr(block, "type") and block.type == "tool_use":
                    tool_count += 1

    return tool_count


def _has_tool_calls_in_last_assistant(messages: list[Message]) -> bool:
    """Check if the last assistant message contains tool calls."""
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.has_tool_use
    return False


def should_extract_memory(
    messages: list[Message],
    *,
    conversation_id: str | None = None,
) -> bool:
    """Decide whether to trigger session memory extraction.

    Mirrors Claude Code's logic:
    - Must meet initialization threshold (10k tokens)
    - Token threshold is ALWAYS required
    - Triggers when: (tokens AND tool_calls) OR (tokens AND no-tool-in-last-turn)
    """
    state = _get_state(conversation_id)
    config = state.config
    current_tokens = _estimate_tokens(messages)

    if not state.initialized:
        if current_tokens < config.min_tokens_to_init:
            return False
        state.initialized = True

    # Token growth since last extraction
    tokens_since = current_tokens - state.tokens_at_last_extraction
    has_met_token_threshold = tokens_since >= config.min_tokens_between_updates

    if not has_met_token_threshold:
        return False

    # Tool calls since last extraction
    tool_calls_since = _count_tool_calls_since(
        messages, state.last_memory_message_uuid,
    )
    has_met_tool_threshold = tool_calls_since >= config.tool_calls_between_updates

    # Natural break: last assistant has no tool calls
    has_tools_in_last = _has_tool_calls_in_last_assistant(messages)

    return has_met_tool_threshold or not has_tools_in_last


# ── Section analysis ────────────────────────────────────────────────

def _analyze_sections(content: str) -> dict[str, int]:
    """Parse markdown sections and estimate their token counts."""
    sections: dict[str, int] = {}
    lines = content.split("\n")
    current_section = ""
    current_lines: list[str] = []

    for line in lines:
        if line.startswith("# "):
            if current_section and current_lines:
                text = "\n".join(current_lines).strip()
                sections[current_section] = len(text) // 4
            current_section = line
            current_lines = []
        else:
            current_lines.append(line)

    if current_section and current_lines:
        text = "\n".join(current_lines).strip()
        sections[current_section] = len(text) // 4

    return sections


def _generate_section_warnings(content: str) -> str:
    """Generate warnings for oversized sections."""
    config = _shared_memory_config
    sections = _analyze_sections(content)
    total_tokens = sum(sections.values())

    warnings: list[str] = []

    if total_tokens > config.max_total_tokens:
        warnings.append(
            f"\nCRITICAL: Session memory is ~{total_tokens} tokens "
            f"(max {config.max_total_tokens}). Condense aggressively."
        )

    oversized = [
        (name, tokens)
        for name, tokens in sections.items()
        if tokens > config.max_section_tokens
    ]
    if oversized:
        items = "\n".join(
            f"- {name}: ~{tokens} tokens (limit: {config.max_section_tokens})"
            for name, tokens in sorted(oversized, key=lambda x: -x[1])
        )
        warnings.append(f"\nOversized sections to condense:\n{items}")

    return "\n".join(warnings)


# ── File management ─────────────────────────────────────────────────

def get_memory_dir(session_id: str = "") -> Path:
    """Get or create the session memory directory."""
    override = os.environ.get("SESSION_MEMORY_DIR", "").strip()
    if override:
        base = Path(override)
    else:
        base = mini_agent_path("session_memory")
    if session_id:
        return base / session_id
    return base


def get_memory_path(session_id: str = "") -> Path:
    """Get the session memory file path."""
    return get_memory_dir(session_id) / "session_notes.md"


async def _setup_memory_file(session_id: str = "") -> tuple[str, str]:
    """Create the memory file if needed, return (path, current_content)."""
    memory_dir = get_memory_dir(session_id)
    memory_dir.mkdir(parents=True, exist_ok=True)

    memory_path = get_memory_path(session_id)

    if not memory_path.exists():
        template = _load_template()
        memory_path.write_text(template, encoding="utf-8")
        return str(memory_path), template

    content = memory_path.read_text(encoding="utf-8")
    return str(memory_path), content


def _load_template() -> str:
    """Load custom template or use default."""
    custom_path = mini_agent_path("session-memory", "template.md")
    if custom_path.exists():
        try:
            return custom_path.read_text(encoding="utf-8")
        except Exception:
            pass
    return DEFAULT_TEMPLATE


# ── Core extraction ─────────────────────────────────────────────────

def build_update_prompt(
    current_notes: str,
    notes_path: str,
) -> str:
    """Build the prompt for the session memory update agent."""
    config = _shared_memory_config
    section_warnings = _generate_section_warnings(current_notes)

    return UPDATE_PROMPT_TEMPLATE.format(
        notes_path=notes_path,
        current_notes=current_notes,
        max_section_tokens=config.max_section_tokens,
        section_warnings=section_warnings,
    )


class _SessionNotesWriteTool(Tool):
    """Restricted file_write wrapper for the session notes file."""

    name = "Write"
    description = "Overwrite the session notes file with updated markdown."
    is_read_only = False

    def __init__(self, notes_path: str) -> None:
        self._notes_path = str(Path(notes_path).resolve())

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Must be the session notes file path."},
                "content": {"type": "string", "description": "Complete updated markdown for the notes file."},
            },
            "required": ["file_path", "content"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        from ..tools.file_write import FileWriteTool

        path = str(Path(kwargs["file_path"]).resolve())
        if path != self._notes_path:
            return f"Error: Access denied. You may only write {self._notes_path}"
        tool = FileWriteTool(allowed_dirs=[str(Path(self._notes_path).parent)])
        return await tool.execute(
            context=context,
            file_path=self._notes_path,
            content=str(kwargs["content"]),
        )


async def _run_notes_update_agent(
    *,
    messages: list[Message],
    provider: BaseProvider,
    notes_path: str,
    current_notes: str,
    full_prompt: str,
) -> str | None:
    from ..delegation.subagent import ForkedAgentContext, run_forked_agent

    tool = _SessionNotesWriteTool(notes_path)
    result = await run_forked_agent(
        context=ForkedAgentContext(
            parent_messages=messages,
            parent_system_prompt=(
                "You are a session notes updater. Your only job is to update "
                "the session notes file using the Write tool."
            ),
            can_use_tool=lambda tool_name: tool_name == "Write",
        ),
        fork_prompt=(
            f"{full_prompt}\n\n"
            f"You MUST use Write exactly once on {notes_path} and then stop. "
            "Write the complete updated markdown content."
        ),
        provider=provider,
        tools=[tool],
        max_turns=3,
        agent_id="session-memory",
    )
    if result.aborted:
        return None
    updated = Path(notes_path).read_text(encoding="utf-8")
    if updated.strip() == current_notes.strip():
        return None
    return updated


async def extract_session_memory(
    messages: list[Message],
    provider: BaseProvider,
    *,
    session_id: str = "",
    file_edit_fn: Any | None = None,
) -> str | None:
    """Run session memory extraction using a side query.

    Uses a forked side-query over the parent conversation so the
    extraction branch does not mutate the main session history.

    Returns the updated notes content, or None on failure.
    """
    state = _get_state(session_id)
    state.mark_extraction_started()

    try:
        notes_path, current_notes = await _setup_memory_file(session_id)
        state.memory_path = notes_path
        state.current_content = current_notes

        # Build the update prompt
        update_prompt = build_update_prompt(current_notes, notes_path)

        # Prepare conversation context (trimmed)
        conv_text = _render_conversation_for_memory(messages)

        full_prompt = (
            f"Here is the conversation so far:\n\n{conv_text}\n\n"
            f"---\n\n{update_prompt}"
        )

        updated = await _run_notes_update_agent(
            messages=messages,
            provider=provider,
            notes_path=notes_path,
            current_notes=current_notes,
            full_prompt=full_prompt,
        )

        if updated and "# " in updated:
            state.current_content = updated
            token_count = _estimate_tokens(messages)
            last_uuid = ""
            if messages:
                last_uuid = messages[-1].metadata.get("uuid", "")
            state.record_extraction(token_count, last_uuid)
            update_last_summarized_safely(messages, conversation_id=session_id)
            logger.info("Session memory updated: %s", notes_path)
            return updated

        logger.warning("Session memory extraction returned empty or invalid content")
        return None

    except Exception as exc:
        logger.error("Session memory extraction failed: %s", exc)
        return None
    finally:
        state.mark_extraction_completed()


def _render_conversation_for_memory(
    messages: list[Message],
    max_chars: int = 50_000,
) -> str:
    """Render recent conversation as text for the memory updater."""
    parts: list[str] = []
    total = 0

    for msg in reversed(messages):
        role = msg.role.upper()
        text = msg.text.strip()
        if not text:
            continue
        entry = f"[{role}]: {text[:3000]}"
        if total + len(entry) > max_chars:
            break
        parts.append(entry)
        total += len(entry)

    parts.reverse()
    return "\n\n".join(parts)


# ── Content access ──────────────────────────────────────────────────

def get_session_memory_content(conversation_id: str | None = None) -> str | None:
    """Get session memory markdown for compact / query injection.

    State is **per** ``conversation_id`` (including ``\"\"`` for legacy no-id hosts).
    """
    state = _get_state(conversation_id)
    if state.current_content:
        return state.current_content

    expected = get_memory_path(conversation_id or "")
    if expected.exists():
        try:
            content = expected.read_text(encoding="utf-8")
            state.memory_path = str(expected)
            state.current_content = content
            return content
        except Exception:
            pass

    if state.memory_path:
        path = Path(state.memory_path)
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
                state.current_content = content
                return content
            except Exception:
                pass
    return None


def is_session_memory_empty(content: str) -> bool:
    """Check if session memory is still just the template."""
    return content.strip() == DEFAULT_TEMPLATE.strip()


def truncate_for_compact(content: str, max_tokens: int = 2000) -> str:
    """Truncate session memory sections for injection into compact summary."""
    max_chars = max_tokens * 4
    lines = content.split("\n")
    output_lines: list[str] = []
    current_section_chars = 0

    for line in lines:
        if line.startswith("# "):
            current_section_chars = 0
            output_lines.append(line)
        else:
            current_section_chars += len(line) + 1
            if current_section_chars <= max_chars:
                output_lines.append(line)
            elif not output_lines[-1].startswith("[..."):
                output_lines.append("[... section truncated ...]")

    return "\n".join(output_lines)


# ── Wait helper ─────────────────────────────────────────────────────

async def wait_for_extraction(
    timeout: float = 15.0,
    *,
    conversation_id: str | None = None,
) -> None:
    """Wait for in-progress extraction to complete for this session."""
    state = _get_state(conversation_id)
    start = time.monotonic()
    while state.extraction_in_progress:
        elapsed = time.monotonic() - state.extraction_started_at
        if elapsed > 60.0:
            return  # stale
        if time.monotonic() - start > timeout:
            return
        await asyncio.sleep(1.0)


# ── Post-sampling hook integration ──────────────────────────────────

class SessionMemoryHook(PostSamplingHook):
    """Post-sampling hook that triggers session memory updates.

    Register with the hook runner to automatically maintain session
    notes after each model response.
    """

    def __init__(
        self,
        provider: BaseProvider,
        *,
        session_id: str = "",
        config: SessionMemoryConfig | None = None,
    ) -> None:
        self._provider = provider
        self._session_id = session_id
        if config:
            global _shared_memory_config
            _shared_memory_config = config
            for st in _states.values():
                st.config = config

    async def on_post_sampling(
        self,
        context: PostSamplingContext,
        *,
        agent: Any = None,
    ) -> None:
        """Check thresholds and trigger extraction if needed."""
        if context.query_source not in ("sdk", "repl_main_thread"):
            return

        sid = self._session_id or (
            str(getattr(agent, "conversation_id", "") or "") if agent is not None else ""
        )
        if not should_extract_memory(context.messages, conversation_id=sid or None):
            return

        # Fire and forget
        asyncio.ensure_future(
            extract_session_memory(
                context.messages,
                self._provider,
                session_id=sid,
            )
        )


# ── Manual trigger ──────────────────────────────────────────────────

@dataclass(slots=True)
class ManualExtractionResult:
    """Result of a manual session memory extraction."""

    success: bool
    memory_path: str = ""
    content: str = ""
    error: str = ""


async def manually_extract_session_memory(
    messages: list[Message],
    provider: BaseProvider,
    *,
    session_id: str = "",
) -> ManualExtractionResult:
    """Manually trigger session memory extraction, bypassing threshold checks.

    Called from the ``/summary`` command. Forces immediate extraction
    regardless of token/tool-call thresholds.

    Returns a :class:`ManualExtractionResult` with the updated notes content.
    """
    if not messages:
        return ManualExtractionResult(
            success=False,
            error="No messages to summarize",
        )

    state = _get_state(session_id)
    state.mark_extraction_started()

    try:
        notes_path, current_notes = await _setup_memory_file(session_id)
        state.memory_path = notes_path
        state.current_content = current_notes

        update_prompt = build_update_prompt(current_notes, notes_path)
        conv_text = _render_conversation_for_memory(messages)

        full_prompt = (
            f"Here is the conversation so far:\n\n{conv_text}\n\n"
            f"---\n\n{update_prompt}"
        )

        updated = await _run_notes_update_agent(
            messages=messages,
            provider=provider,
            notes_path=notes_path,
            current_notes=current_notes,
            full_prompt=full_prompt,
        )

        if updated and "# " in updated:
            state.current_content = updated
            token_count = _estimate_tokens(messages)
            last_uuid = messages[-1].metadata.get("uuid", "") if messages else ""
            state.record_extraction(token_count, last_uuid)

            update_last_summarized_safely(messages, conversation_id=session_id)

            logger.info("Manual session memory extraction: %s", notes_path)
            return ManualExtractionResult(
                success=True,
                memory_path=notes_path,
                content=updated,
            )

        return ManualExtractionResult(
            success=False,
            error="Extraction returned empty or invalid content",
        )

    except Exception as exc:
        logger.error("Manual session memory extraction failed: %s", exc)
        return ManualExtractionResult(success=False, error=str(exc))
    finally:
        state.mark_extraction_completed()


# ── Enhanced prompt template ────────────────────────────────────────

ENHANCED_TEMPLATE_SECTIONS = (
    "INSTRUCTIONS",
    "CURRENT_SESSION_MEMORY",
    "CONVERSATION",
    "OUTPUT_FORMAT",
)

_SESSION_MEMORY_PROMPT_PATH = (
    mini_agent_path("session_memory_prompt.md")
)


def load_session_memory_template() -> str:
    """Load a custom session-memory prompt template, or fall back to built-in.

    Checks ``~/.mini_agent/session_memory_prompt.md`` first.  A custom
    template should contain the sections: INSTRUCTIONS,
    CURRENT_SESSION_MEMORY, CONVERSATION, OUTPUT_FORMAT (all optional —
    missing sections are filled from the built-in default).

    Returns the template string (with ``{placeholders}`` for variable
    substitution).
    """
    if _SESSION_MEMORY_PROMPT_PATH.exists():
        try:
            custom = _SESSION_MEMORY_PROMPT_PATH.read_text(encoding="utf-8")
            if custom.strip():
                return custom
        except Exception:
            logger.debug(
                "Failed to read custom session memory template at %s",
                _SESSION_MEMORY_PROMPT_PATH,
            )

    return _default_enhanced_template()


def _default_enhanced_template() -> str:
    """Built-in enhanced template with all four sections."""
    return """\
## INSTRUCTIONS

IMPORTANT: This message is NOT part of the user conversation. Do NOT \
reference note-taking in the notes content.

Based on the conversation, update the session notes file.
Your ONLY task: update the notes, then stop.

RULES:
- NEVER modify section headers (# lines) or italic descriptions (_ lines)
- ONLY update content BELOW each section's italic description
- Write DETAILED, INFO-DENSE content: file paths, function names, commands
- Keep each section under ~{max_section_tokens} tokens
- Always update "Current State" to reflect the most recent work
- Skip sections with no new insights (don't add filler)
- For "Key Results", include complete exact output the user requested
- Do not reference these instructions in the notes

## CURRENT_SESSION_MEMORY

The file {notes_path} has current contents:
<current_notes>
{current_notes}
</current_notes>

## CONVERSATION

The conversation will be provided separately.

## OUTPUT_FORMAT

Output ONLY the complete updated markdown content for the notes file.
Maintain all section headers and italic descriptions exactly as they are.
{section_warnings}
"""


# ── Section analysis (public, char-based) ───────────────────────────

MAX_SECTION_LENGTH = 4000


def analyze_section_sizes(content: str) -> dict[str, int]:
    """Parse session memory by markdown headings and return character counts.

    Returns a dict mapping ``# Section Name`` → character count of the
    body text under that heading. Used to detect sections growing too
    large (see :func:`generate_section_reminders`).
    """
    sections: dict[str, int] = {}
    lines = content.split("\n")
    current_section = ""
    current_lines: list[str] = []

    for line in lines:
        if line.startswith("# "):
            if current_section:
                text = "\n".join(current_lines).strip()
                sections[current_section] = len(text)
            current_section = line
            current_lines = []
        else:
            current_lines.append(line)

    if current_section:
        text = "\n".join(current_lines).strip()
        sections[current_section] = len(text)

    return sections


# ── Section reminders ───────────────────────────────────────────────

def generate_section_reminders(
    content: str,
    max_section_length: int = MAX_SECTION_LENGTH,
) -> str:
    """Generate consolidation reminders for oversized sections.

    Any section exceeding *max_section_length* characters gets a warning
    line suggesting the user condense it.  Returns an empty string when
    all sections are within budget.
    """
    sizes = analyze_section_sizes(content)
    reminders: list[str] = []

    for section, char_count in sorted(sizes.items(), key=lambda x: -x[1]):
        if char_count > max_section_length:
            reminders.append(
                f"Section '{section}' is getting large ({char_count} chars). "
                "Consider consolidating."
            )

    return "\n".join(reminders)


# ── Safe cursor update ──────────────────────────────────────────────

def update_last_summarized_safely(
    messages: list[Message],
    *,
    conversation_id: str | None = None,
) -> None:
    """Update the message-ID cursor only when no tool calls are in progress.

    Prevents accidentally skipping messages during active tool execution.
    Mirrors Claude Code's ``updateLastSummarizedMessageIdIfSafe``.
    """
    if _has_tool_calls_in_last_assistant(messages):
        return

    state = _get_state(conversation_id)
    if messages:
        last = messages[-1]
        uuid = last.metadata.get("uuid", "")
        if uuid:
            state.last_memory_message_uuid = uuid


# ── Flush a single section ──────────────────────────────────────────

def flush_session_section(
    section_name: str,
    *,
    conversation_id: str | None = None,
) -> bool:
    """Remove a specific section's content from the current session memory.

    The section header and italic description are preserved, but all body
    text is cleared.  Useful during compact to prune stale sections.

    Returns ``True`` if the section was found and flushed, ``False``
    otherwise.
    """
    state = _get_state(conversation_id)
    content = state.current_content
    if not content:
        return False

    lines = content.split("\n")
    output: list[str] = []
    in_target = False
    flushed = False

    for line in lines:
        if line.startswith("# "):
            in_target = line.strip() == section_name.strip()
            if in_target:
                flushed = True
            output.append(line)
        elif in_target:
            if line.startswith("_") and line.endswith("_"):
                output.append(line)
            # else: skip body lines (flush)
        else:
            output.append(line)

    if flushed:
        new_content = "\n".join(output)
        state.current_content = new_content
        if state.memory_path:
            try:
                Path(state.memory_path).write_text(new_content, encoding="utf-8")
            except OSError as exc:
                logger.warning("Failed to flush section %r: %s", section_name, exc)
                return False

    return flushed


# ── Remote config support ───────────────────────────────────────────

@dataclass(slots=True)
class SessionMemoryRemoteConfig:
    """Remote/file-based config overrides for session memory thresholds.

    Loaded from ``~/.mini_agent/session_memory_config.json``.  Any field
    set to a positive value overrides the corresponding
    :class:`SessionMemoryConfig` default.
    """

    token_threshold: int = 0
    tool_call_threshold: int = 0
    extraction_model: str = ""


_REMOTE_CONFIG_PATH = mini_agent_path("session_memory_config.json")


def load_remote_config() -> SessionMemoryRemoteConfig:
    """Read session memory config overrides from disk.

    Returns a :class:`SessionMemoryRemoteConfig` populated from
    ``~/.mini_agent/session_memory_config.json`` if the file exists and
    is valid JSON; otherwise returns the zero-value default (no
    overrides).
    """
    if not _REMOTE_CONFIG_PATH.exists():
        return SessionMemoryRemoteConfig()

    try:
        raw = _REMOTE_CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        return SessionMemoryRemoteConfig(
            token_threshold=int(data.get("token_threshold", 0)),
            tool_call_threshold=int(data.get("tool_call_threshold", 0)),
            extraction_model=str(data.get("extraction_model", "")),
        )
    except (json.JSONDecodeError, OSError, ValueError, TypeError) as exc:
        logger.debug("Failed to load remote session memory config: %s", exc)
        return SessionMemoryRemoteConfig()


def apply_remote_config(remote: SessionMemoryRemoteConfig) -> None:
    """Apply non-zero fields from *remote* onto the active config."""
    config = _shared_memory_config
    if remote.token_threshold > 0:
        config.min_tokens_between_updates = remote.token_threshold
    if remote.tool_call_threshold > 0:
        config.tool_calls_between_updates = remote.tool_call_threshold
