"""Magic Docs — automatically maintained documentation files.

Ported from Claude Code's ``MagicDocs`` subsystem:
- Files with ``# MAGIC DOC: [title]`` headers are auto-tracked
- When the conversation is idle (no tool calls), tracked docs are
  updated via a background side query or a forked agent
- Automatic detection via ``MagicDocListener`` on file reads
- Optional instructions line (italics) after the header
- Supports custom prompt templates from ``~/.mini_agent/magic-docs/prompt.md``
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..hooks import PostSamplingHook
from ..messages import Message, user_message
from ..paths import mini_agent_path
from ..tool import Tool, ToolUseContext

if TYPE_CHECKING:
    from ..providers import BaseProvider
    from ..hooks import PostSamplingContext

logger = logging.getLogger(__name__)


# ── Header detection ────────────────────────────────────────────────

MAGIC_DOC_HEADER_RE = re.compile(
    r"^#\s*MAGIC\s+DOC:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)

ITALICS_RE = re.compile(r"^[_*](.+?)[_*]\s*$", re.MULTILINE)


@dataclass(slots=True)
class MagicDocInfo:
    """Tracked magic document."""
    path: str
    title: str = ""
    instructions: str | None = None


def detect_magic_doc_header(
    content: str,
) -> dict[str, str] | None:
    """Detect a Magic Doc header in file content.

    Returns ``{"title": ..., "instructions": ...}`` or ``None``.
    """
    match = MAGIC_DOC_HEADER_RE.search(content)
    if not match:
        return None

    title = match.group(1).strip()

    after = content[match.end():]
    next_match = re.match(r"\s*\n(?:\s*\n)?(.+?)(?:\n|$)", after)
    instructions: str | None = None
    if next_match:
        line = next_match.group(1).strip()
        ital = ITALICS_RE.match(line)
        if ital:
            instructions = ital.group(1).strip()

    return {"title": title, "instructions": instructions}


# ── Tracking ────────────────────────────────────────────────────────

_tracked_docs: dict[str, MagicDocInfo] = {}


def register_magic_doc(filepath: str, title: str = "", instructions: str | None = None) -> None:
    """Register a file as a Magic Doc (idempotent)."""
    if filepath not in _tracked_docs:
        _tracked_docs[filepath] = MagicDocInfo(
            path=filepath, title=title, instructions=instructions,
        )


def unregister_magic_doc(filepath: str) -> None:
    _tracked_docs.pop(filepath, None)


def get_tracked_docs() -> dict[str, MagicDocInfo]:
    return dict(_tracked_docs)


def clear_tracked_docs() -> None:
    _tracked_docs.clear()


def clear_tracked_magic_docs() -> None:
    """Clear all registered magic docs — alias called during session reset."""
    _tracked_docs.clear()


def check_and_register(filepath: str, content: str) -> bool:
    """Check if content has a magic doc header and register if so."""
    detected = detect_magic_doc_header(content)
    if detected:
        register_magic_doc(
            filepath,
            title=detected["title"],
            instructions=detected.get("instructions"),
        )
        return True
    return False


# ── FileReadListener integration ────────────────────────────────────

_file_read_listeners: list[Callable[[str, str], None]] = []
_listener_initialized = False


class MagicDocListener:
    """Automatically detect magic doc headers when files are read.

    Register an instance via ``register_file_read_listener`` so that
    every file read is checked for a ``# MAGIC DOC:`` header.  If found
    the file is auto-registered — no manual ``register_magic_doc`` call
    needed.
    """

    def on_file_read(self, path: str, content: str) -> None:
        """Called whenever a file is read by the tool layer."""
        detected = detect_magic_doc_header(content)
        if detected:
            register_magic_doc(
                path,
                title=detected["title"],
                instructions=detected.get("instructions"),
            )


def register_file_read_listener(listener: Callable[[str, str], None]) -> None:
    """Register a listener that is called on every file read."""
    _file_read_listeners.append(listener)


def notify_file_read(path: str, content: str) -> None:
    """Notify all registered listeners that a file was read."""
    for listener in _file_read_listeners:
        try:
            listener(path, content)
        except Exception as exc:
            logger.debug("File read listener error: %s", exc)


def init_magic_doc_listener() -> None:
    """Set up the ``MagicDocListener`` so reads auto-register docs."""
    global _listener_initialized
    if _listener_initialized:
        return
    listener = MagicDocListener()
    register_file_read_listener(listener.on_file_read)
    _listener_initialized = True


# ── Update prompt (enhanced DOCUMENTATION PHILOSOPHY) ───────────────

DEFAULT_UPDATE_PROMPT = """\
IMPORTANT: This message and these instructions are NOT part of the actual \
user conversation. Do NOT include any references to "documentation updates", \
"magic docs", or these update instructions in the document content.

Based on the user conversation above (EXCLUDING this documentation update \
instruction message), update the Magic Doc file to incorporate any NEW \
learnings, insights, or information that would be valuable to preserve.

File: {{docPath}}
Title: {{docTitle}}
{{customInstructions}}

Current contents:
<current_doc_content>
{{docContents}}
</current_doc_content>

Your ONLY task is to produce updated document content if there is substantial \
new information to add. If there's nothing substantial, respond with \
"NO_CHANGES".

CRITICAL RULES FOR EDITING:
- Preserve the Magic Doc header exactly as-is: # MAGIC DOC: {{docTitle}}
- If there's an italicized line immediately after the header, preserve it \
exactly as-is
- Keep the document CURRENT with the latest state of the codebase — this is \
NOT a changelog or history
- Update information IN-PLACE to reflect the current state — do NOT append \
historical notes or track changes over time
- Remove or replace outdated information rather than adding "Previously..." \
or "Updated to..." notes
- Clean up or DELETE sections that are no longer relevant or don't align with \
the document's purpose
- Fix obvious errors: typos, grammar mistakes, broken formatting, incorrect \
information, or confusing statements
- Keep the document well organized: use clear headings, logical section \
order, consistent formatting, and proper nesting

DOCUMENTATION PHILOSOPHY — READ CAREFULLY:
- BE TERSE. High signal only. No filler words or unnecessary elaboration.
- Documentation is for OVERVIEWS, ARCHITECTURE, and ENTRY POINTS — not \
detailed code walkthroughs
- Do NOT duplicate information that's already obvious from reading the \
source code
- Do NOT document every function, parameter, or line number reference
- Focus on: WHY things exist, HOW components connect, WHERE to start \
reading, WHAT patterns are used
- Skip: detailed implementation steps, exhaustive API docs, play-by-play \
narratives

What TO document:
- High-level architecture and system design
- Non-obvious patterns, conventions, or gotchas
- Key entry points and where to start reading code
- Important design decisions and their rationale
- Critical dependencies or integration points
- References to related files, docs, or code (like a wiki)

What NOT to document:
- Anything obvious from reading the code itself
- Exhaustive lists of files, functions, or parameters
- Step-by-step implementation details
- Low-level code mechanics
- Information already in CLAUDE.md or other project docs

REMEMBER: Only update if there is substantial new information. \
The Magic Doc header (# MAGIC DOC: {{docTitle}}) must remain unchanged."""


def _load_custom_prompt() -> str:
    """Load custom prompt template if it exists."""
    custom_path = mini_agent_path("magic-docs", "prompt.md")
    try:
        return custom_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return DEFAULT_UPDATE_PROMPT


def _substitute_vars(template: str, variables: dict[str, str]) -> str:
    """Replace {{variable}} placeholders in a template.

    Uses a single-pass replacement to avoid backreference corruption and
    double-substitution when user content contains ``{{varName}}``.
    """
    def replacer(match: re.Match[str]) -> str:
        key = match.group(1)
        return variables.get(key, match.group(0))
    return re.sub(r"\{\{(\w+)\}\}", replacer, template)


def build_update_prompt(
    doc_contents: str,
    doc_path: str,
    doc_title: str,
    instructions: str | None = None,
) -> str:
    """Build the Magic Docs update prompt with variable substitution."""
    template = _load_custom_prompt()

    custom = ""
    if instructions:
        custom = (
            "\n\nDOCUMENT-SPECIFIC UPDATE INSTRUCTIONS:\n"
            "The document author has provided specific instructions for how "
            "this file should be updated. Pay extra attention to these "
            "instructions and follow them carefully:\n\n"
            f'"{instructions}"\n\n'
            "These instructions take priority over the general rules below. "
            "Make sure your updates align with these specific guidelines."
        )

    return _substitute_vars(template, {
        "docContents": doc_contents,
        "docPath": doc_path,
        "docTitle": doc_title,
        "customInstructions": custom,
    })


# ── Core update logic (side_query) ──────────────────────────────────

async def update_magic_doc(
    doc_info: MagicDocInfo,
    messages: list[Message],
    provider: BaseProvider,
) -> bool:
    """Update a single Magic Doc via side_query. Returns True if modified."""
    try:
        current = Path(doc_info.path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        unregister_magic_doc(doc_info.path)
        return False

    detected = detect_magic_doc_header(current)
    if not detected:
        unregister_magic_doc(doc_info.path)
        return False

    title = detected["title"]
    instructions = detected.get("instructions")

    prompt = build_update_prompt(current, doc_info.path, title, instructions)

    conv_parts: list[str] = []
    for msg in messages[-30:]:
        text = msg.text.strip()[:2000]
        if text:
            conv_parts.append(f"[{msg.role.upper()}]: {text}")
    conversation = "\n\n".join(conv_parts)

    full_prompt = f"{conversation}\n\n---\n\n{prompt}"

    from .side_query import side_query, SideQueryOptions

    try:
        result = await side_query(
            provider,
            SideQueryOptions(
                system="You are a documentation maintenance agent. Update "
                       "Magic Doc files based on conversation context.",
                messages=[user_message(full_prompt)],
                max_tokens=4096,
                temperature=0.0,
                query_source="magic_docs",
            ),
        )

        new_content = result.text.strip()
        if not new_content or "NO_CHANGES" in new_content:
            return False

        if not detect_magic_doc_header(new_content):
            logger.warning("Updated content missing magic doc header, skipping")
            return False

        Path(doc_info.path).write_text(new_content, encoding="utf-8")
        logger.info("Updated Magic Doc: %s", doc_info.path)
        return True

    except Exception as exc:
        logger.error("Failed to update Magic Doc %s: %s", doc_info.path, exc)
        return False


# ── Agent-based update (forked agent with Edit tool) ────────────────

async def update_magic_doc_with_agent(
    doc_info: MagicDocInfo,
    messages: list[Message],
    provider: BaseProvider,
    *,
    system_prompt: str = "",
) -> bool:
    """Update a Magic Doc using a forked agent that can make surgical edits.
    """
    try:
        current = Path(doc_info.path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        unregister_magic_doc(doc_info.path)
        return False

    detected = detect_magic_doc_header(current)
    if not detected:
        unregister_magic_doc(doc_info.path)
        return False

    title = detected["title"]
    instructions = detected.get("instructions")
    prompt = build_update_prompt(current, doc_info.path, title, instructions)

    try:
        from ..delegation.subagent import ForkedAgentContext, run_forked_agent

        class _MagicDocWriteTool(Tool):
            name = "Write"
            description = "Overwrite the target Magic Doc file."
            is_read_only = False

            def __init__(self, path: str) -> None:
                self._path = str(Path(path).resolve())

            def get_parameters_schema(self) -> dict[str, Any]:
                return {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["file_path", "content"],
                }

            async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
                from ..tools.file_write import FileWriteTool

                path = str(Path(kwargs["file_path"]).resolve())
                if path != self._path:
                    return f"Error: Access denied. You may only write {self._path}"
                tool = FileWriteTool(allowed_dirs=[str(Path(self._path).parent)])
                return await tool.execute(
                    context=context,
                    file_path=self._path,
                    content=str(kwargs["content"]),
                )

        tool = _MagicDocWriteTool(doc_info.path)
        result = await run_forked_agent(
            context=ForkedAgentContext(
                parent_messages=messages[-30:],
                parent_system_prompt=system_prompt or (
                    "You are a documentation maintenance agent. "
                    "Use the Write tool to update the Magic Doc file and nothing else."
                ),
                can_use_tool=lambda tool_name: tool_name == "Write",
            ),
            fork_prompt=(
                f"{prompt}\n\n"
                f"You MUST use Write exactly once on {doc_info.path} and then stop. "
                "Write the complete updated document content."
            ),
            provider=provider,
            tools=[tool],
            max_turns=3,
            agent_id="magic-doc",
        )
        if result.aborted:
            return False

        new_content = Path(doc_info.path).read_text(encoding="utf-8")
        if new_content.strip() == current.strip():
            return False
        if not detect_magic_doc_header(new_content):
            logger.warning("Updated content missing magic doc header, skipping")
            return False
        logger.info("Agent-based update completed for Magic Doc: %s", doc_info.path)
        return True

    except Exception as exc:
        logger.error(
            "Agent-based Magic Doc update failed for %s: %s",
            doc_info.path, exc,
        )
        return False


async def update_all_magic_docs(
    messages: list[Message],
    provider: BaseProvider,
    *,
    use_agent: bool = False,
    system_prompt: str = "",
) -> list[str]:
    """Update all tracked Magic Docs. Returns list of paths updated."""
    if not _tracked_docs:
        return []

    updated: list[str] = []
    for doc_info in list(_tracked_docs.values()):
        if use_agent:
            success = await update_magic_doc_with_agent(
                doc_info, messages, provider, system_prompt=system_prompt,
            )
        else:
            success = await update_magic_doc(doc_info, messages, provider)
        if success:
            updated.append(doc_info.path)

    return updated


# ── Hook integration ────────────────────────────────────────────────

def _has_tool_calls_in_last_assistant(messages: list[Message]) -> bool:
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.has_tool_use
    return False


class MagicDocsHook(PostSamplingHook):
    """Post-sampling hook that updates tracked Magic Docs.

    Only fires when the conversation is idle (no tool calls in last turn).
    """

    def __init__(self, provider: BaseProvider, *, use_agent: bool = True) -> None:
        self._provider = provider
        self._use_agent = use_agent

    async def on_post_sampling(
        self,
        context: PostSamplingContext,
        *,
        agent: Any = None,
    ) -> None:
        if context.query_source not in ("sdk", "repl_main_thread"):
            return

        if _has_tool_calls_in_last_assistant(context.messages):
            return

        if not _tracked_docs:
            return

        asyncio.ensure_future(
            update_all_magic_docs(
                context.messages,
                self._provider,
                use_agent=self._use_agent,
            )
        )
