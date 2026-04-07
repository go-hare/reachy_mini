"""Extract Memories — background extraction of durable memories from conversation.

Ported from Claude Code's ``extractMemories`` subsystem:
- Runs at the end of each complete query loop (when model produces a
  final response with no tool calls)
- Extracts key information into persistent memory files
- Uses memory types: user, feedback, project, reference
- Overlap guard prevents concurrent extractions
- Trailing-run pattern processes stashed contexts after completion
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..hooks import PostSamplingHook
from ..messages import Message, system_message
from ..tool import Tool, ToolUseContext
from .memdir import (
    MEMORY_FRONTMATTER_EXAMPLE,
    TYPES_DESCRIPTION,
    WHAT_NOT_TO_SAVE,
    format_memory_manifest,
    get_memory_dir,
    is_memory_path,
    scan_memory_files,
)

if TYPE_CHECKING:
    from ..providers import BaseProvider
    from ..hooks import PostSamplingContext

logger = logging.getLogger(__name__)


class _MemoryInspectTool(Tool):
    """Restricted read-only inspector for existing memory files."""

    name = "memory_inspect"
    description = "List, read, or search existing files inside the memory directory."
    is_read_only = True

    _MAX_LIST_ENTRIES = 200
    _MAX_READ_CHARS = 12_000
    _MAX_GREP_MATCHES = 60

    def __init__(self, memory_dir: str) -> None:
        self._memory_dir = Path(memory_dir).resolve()

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "read", "grep"],
                },
                "path": {
                    "type": "string",
                    "description": "Relative file or directory path inside the memory directory.",
                },
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for when action=grep.",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Whether grep should be case-sensitive.",
                },
            },
            "required": ["action"],
        }

    def _resolve_target(self, relative: str = "") -> Path:
        target = (self._memory_dir / relative).resolve() if relative else self._memory_dir
        try:
            target.relative_to(self._memory_dir)
        except ValueError as exc:
            raise ValueError(f"Path must stay inside {self._memory_dir}") from exc
        return target

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        del context
        action = str(kwargs.get("action", "")).strip().lower()
        relative = str(kwargs.get("path", "")).strip()
        try:
            if action == "list":
                target = self._resolve_target(relative)
                if not target.exists():
                    return f"Error: {target} does not exist."
                if target.is_file():
                    return f"FILE {target.relative_to(self._memory_dir).as_posix()}"
                entries: list[str] = []
                for child in sorted(target.rglob("*")):
                    if len(entries) >= self._MAX_LIST_ENTRIES:
                        entries.append("... truncated ...")
                        break
                    kind = "DIR" if child.is_dir() else "FILE"
                    entries.append(f"{kind} {child.relative_to(self._memory_dir).as_posix()}")
                return "\n".join(entries) if entries else "(empty)"

            if action == "read":
                target = self._resolve_target(relative)
                if not target.is_file():
                    return f"Error: {target} is not a file."
                text = target.read_text(encoding="utf-8", errors="replace")
                if len(text) > self._MAX_READ_CHARS:
                    text = f"{text[:self._MAX_READ_CHARS]}\n... truncated ..."
                return f"# {target.relative_to(self._memory_dir).as_posix()}\n\n{text}"

            if action == "grep":
                pattern = str(kwargs.get("pattern", "")).strip()
                if not pattern:
                    return "Error: pattern is required for grep."
                target = self._resolve_target(relative)
                if not target.exists():
                    return f"Error: {target} does not exist."
                flags = 0 if bool(kwargs.get("case_sensitive", False)) else re.IGNORECASE
                regex = re.compile(pattern, flags)
                files = [target] if target.is_file() else [child for child in sorted(target.rglob("*")) if child.is_file()]
                matches: list[str] = []
                for file_path in files:
                    try:
                        text = file_path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    for line_no, line in enumerate(text.splitlines(), start=1):
                        if regex.search(line):
                            snippet = line.strip()
                            if len(snippet) > 220:
                                snippet = f"{snippet[:220]}..."
                            matches.append(
                                f"{file_path.relative_to(self._memory_dir).as_posix()}:{line_no}: {snippet}"
                            )
                            if len(matches) >= self._MAX_GREP_MATCHES:
                                matches.append("... truncated ...")
                                return "\n".join(matches)
                return "\n".join(matches) if matches else "(no matches)"

            return f"Error: Unsupported action: {action}"
        except ValueError as exc:
            return f"Error: {exc}"
        except OSError as exc:
            return f"Error inspecting memory files: {exc}"


class _MemoryWriteTool(Tool):
    """Restricted writer for memory files under a single memory dir."""

    name = "memory_write"
    description = "Create or overwrite a memory file inside the memory directory."
    is_read_only = False

    def __init__(self, memory_dir: str) -> None:
        self._memory_dir = Path(memory_dir).resolve()
        self.written_paths: list[str] = []

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative memory file path."},
                "content": {"type": "string", "description": "Full markdown content with frontmatter."},
            },
            "required": ["path", "content"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        relative = str(kwargs.get("path", "")).strip()
        content = str(kwargs.get("content", ""))
        if not relative or not content:
            return "Error: path and content are required."

        target = (self._memory_dir / relative).resolve()
        if not str(target).startswith(str(self._memory_dir)):
            return f"Error: Access denied. Path must stay inside {self._memory_dir}"

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return f"Error writing memory file: {exc}"

        path_text = str(target)
        if path_text not in self.written_paths:
            self.written_paths.append(path_text)
        return f"Wrote memory file: {target}"


# ── Prompts ─────────────────────────────────────────────────────────

def _build_extract_prompt(
    new_message_count: int,
    existing_memories: str,
) -> str:
    """Build the extraction prompt for the background agent."""
    manifest = ""
    if existing_memories:
        manifest = (
            f"\n\n## Existing memory files\n\n{existing_memories}\n\n"
            "Check this list before writing — update an existing file "
            "rather than creating a duplicate."
        )

    return f"""\
You are now acting as the memory extraction subagent. Analyze the most \
recent ~{new_message_count} messages above and extract durable memories.

You MUST only use content from the last ~{new_message_count} messages. \
Do not investigate or verify content further.{manifest}

If the user explicitly asks to remember something, save it as whichever \
type fits best. If they ask to forget something, note that for removal.

{TYPES_DESCRIPTION}

{WHAT_NOT_TO_SAVE}

## How to save memories

Write each memory as a JSON object with these fields:
- path: filename for the memory (e.g., "user_preferences.md")
- content: full markdown content with frontmatter

Frontmatter format:
{MEMORY_FRONTMATTER_EXAMPLE}

Respond with a JSON array of memories to save. If nothing worth saving, \
respond with an empty array [].

Example response:
```json
[
  {{
    "path": "user_testing_preference.md",
    "content": "---\\nname: user-testing-preference\\ndescription: User prefers pytest with -v flag\\ntype: feedback\\n---\\n\\nUser prefers running tests with `pytest -v` for verbose output."
  }}
]
```"""


# ── State ───────────────────────────────────────────────────────────

@dataclass
class ExtractMemoriesState:
    """Closure-scoped mutable state for memory extraction."""

    last_message_uuid: str = ""
    in_progress: bool = False
    turns_since_last: int = 0
    extract_interval: int = 1  # extract every N eligible turns
    total_extracted: int = 0
    total_files_written: int = 0


_state = ExtractMemoriesState()


def get_extract_state() -> ExtractMemoriesState:
    return _state


def reset_extract_state() -> None:
    global _state
    _state = ExtractMemoriesState()


# ── Helpers ─────────────────────────────────────────────────────────

def _count_visible_messages_since(
    messages: list[Message],
    since_uuid: str,
) -> int:
    """Count user/assistant messages after a cursor UUID."""
    count = 0
    found = not since_uuid
    for msg in messages:
        if not found:
            if msg.metadata.get("uuid") == since_uuid:
                found = True
            continue
        if msg.role in ("user", "assistant"):
            count += 1
    if not found:
        return sum(1 for m in messages if m.role in ("user", "assistant"))
    return count


def _has_tool_calls_in_last_assistant(messages: list[Message]) -> bool:
    for msg in reversed(messages):
        if msg.role == "assistant":
            return msg.has_tool_use
    return False


def _has_memory_writes_since(
    messages: list[Message],
    since_uuid: str,
    memory_dir: str,
) -> bool:
    """Check if the main agent already wrote to memory files."""
    found = not since_uuid
    for msg in messages:
        if not found:
            if msg.metadata.get("uuid") == since_uuid:
                found = True
            continue
        if msg.role != "assistant" or isinstance(msg.content, str):
            continue
        for block in msg.content:
            if hasattr(block, "name") and hasattr(block, "input"):
                if block.name in ("Edit", "Write"):
                    fp = block.input.get("file_path", "")
                    if is_memory_path(fp, memory_dir):
                        return True
    return False


# ── Core extraction ─────────────────────────────────────────────────

async def extract_memories(
    messages: list[Message],
    provider: BaseProvider,
    *,
    memory_dir: str = "",
    project_root: str = "",
) -> list[str]:
    """Extract durable memories from conversation and write to files.

    Returns list of paths written. This is the main entry point.
    """
    state = _state
    if not memory_dir:
        memory_dir = get_memory_dir(project_root)

    # Skip if main agent already wrote memories
    if _has_memory_writes_since(messages, state.last_message_uuid, memory_dir):
        logger.debug("Skipping extraction — main agent wrote memories")
        if messages:
            state.last_message_uuid = messages[-1].metadata.get("uuid", "")
        return []

    new_count = _count_visible_messages_since(messages, state.last_message_uuid)
    if new_count < 2:
        return []

    # Throttle: only extract every N turns
    state.turns_since_last += 1
    if state.turns_since_last < state.extract_interval:
        return []
    state.turns_since_last = 0

    state.in_progress = True
    start = time.monotonic()

    try:
        # Scan existing memories
        existing = await scan_memory_files(memory_dir)
        manifest = format_memory_manifest(existing)

        # Build prompt
        prompt = _build_extract_prompt(new_count, manifest)

        # Render recent conversation
        conv = _render_recent(messages, max_chars=30_000)
        full_prompt = f"{conv}\n\n---\n\n{prompt}"

        from ..delegation.subagent import ForkedAgentContext, run_forked_agent

        inspector = _MemoryInspectTool(memory_dir)
        writer = _MemoryWriteTool(memory_dir)
        result = await run_forked_agent(
            context=ForkedAgentContext(
                parent_messages=messages,
                parent_system_prompt=(
                    "You are a memory extraction agent. Extract durable "
                    "memories. Use memory_inspect to review existing memory "
                    "files before writing updates with memory_write."
                ),
                can_use_tool=lambda tool_name: tool_name in {
                    "memory_inspect",
                    "memory_write",
                },
            ),
            fork_prompt=(
                f"{full_prompt}\n\n"
                "Use memory_inspect to list, read, and grep existing memory files before "
                "you edit anything. Then use memory_write to create or update each durable "
                "memory file directly. Do not output a JSON list. Write the files directly, "
                "then stop."
            ),
            provider=provider,
            tools=[inspector, writer],
            max_turns=4,
            agent_id="extract-memories",
        )

        written = list(writer.written_paths)

        # Advance cursor
        if messages:
            state.last_message_uuid = messages[-1].metadata.get("uuid", "")
        state.total_extracted += 1
        state.total_files_written += len(written)

        elapsed = time.monotonic() - start
        if written:
            logger.info(
                "Extracted %d memories in %.1fs: %s",
                len(written), elapsed, ", ".join(written),
            )
        else:
            logger.debug("No memories extracted (%.1fs)", elapsed)

        return written

    except Exception as exc:
        logger.error("Memory extraction failed: %s", exc)
        return []
    finally:
        state.in_progress = False


def _render_recent(messages: list[Message], max_chars: int = 30_000) -> str:
    """Render recent messages for the extraction agent."""
    parts: list[str] = []
    total = 0
    for msg in reversed(messages):
        text = msg.text.strip()
        if not text:
            continue
        entry = f"[{msg.role.upper()}]: {text[:2000]}"
        if total + len(entry) > max_chars:
            break
        parts.append(entry)
        total += len(entry)
    parts.reverse()
    return "\n\n".join(parts)

# ── Hook integration ────────────────────────────────────────────────

class ExtractMemoriesHook(PostSamplingHook):
    """Post-sampling hook that triggers memory extraction.

    Register with the hook runner to automatically extract memories
    after the model produces a final response (no tool calls).
    """

    def __init__(
        self,
        provider: BaseProvider,
        *,
        memory_dir: str = "",
        project_root: str = "",
    ) -> None:
        self._provider = provider
        self._memory_dir = memory_dir
        self._project_root = project_root

    async def on_post_sampling(
        self,
        context: PostSamplingContext,
        *,
        agent: Any = None,
    ) -> None:
        """Extract memories when conversation is idle (no pending tool calls)."""
        if context.query_source not in ("sdk", "repl_main_thread"):
            return

        # Only extract when the model is done (no tool calls in last turn)
        if _has_tool_calls_in_last_assistant(context.messages):
            return

        asyncio.ensure_future(
            extract_memories_coalesced(
                context.messages,
                self._provider,
                memory_dir=self._memory_dir,
                project_root=self._project_root,
                agent=agent,
            )
        )


# ── Trailing-run coalescing ─────────────────────────────────────────

@dataclass
class _PendingExtraction:
    """Stashed context for a trailing extraction run.

    When :func:`extract_memories_coalesced` is called while an extraction
    is already in progress, we stash the latest context here.  Once the
    current run finishes, the stashed context is consumed for one
    trailing run — so at most two extractions overlap in time.
    """

    messages: list[Message]
    provider: BaseProvider
    memory_dir: str = ""
    project_root: str = ""
    agent: Any | None = None


_pending: _PendingExtraction | None = None
_in_flight: asyncio.Task[list[str]] | None = None


async def extract_memories_coalesced(
    messages: list[Message],
    provider: BaseProvider,
    *,
    memory_dir: str = "",
    project_root: str = "",
    agent: Any | None = None,
) -> list[str]:
    """Coalescing wrapper around :func:`extract_memories`.

    If an extraction is already running, the request is queued.  When
    the current extraction finishes it picks up the most recent queued
    context for a trailing run.  This prevents redundant concurrent
    extractions while ensuring new messages are still processed.
    """
    global _pending, _in_flight

    if _in_flight is not None and not _in_flight.done():
        _pending = _PendingExtraction(
            messages=messages,
            provider=provider,
            memory_dir=memory_dir,
            project_root=project_root,
            agent=agent,
        )
        logger.debug("Extraction in progress — stashing for trailing run")
        return []

    return await _run_with_trailing(
        messages, provider,
        memory_dir=memory_dir,
        project_root=project_root,
        agent=agent,
    )


async def _run_with_trailing(
    messages: list[Message],
    provider: BaseProvider,
    *,
    memory_dir: str,
    project_root: str,
    agent: Any | None,
) -> list[str]:
    """Run extraction and follow up with a trailing run if pending."""
    global _pending, _in_flight

    async def _do() -> list[str]:
        return await extract_memories(
            messages, provider,
            memory_dir=memory_dir,
            project_root=project_root,
        )

    _in_flight = asyncio.ensure_future(_do())
    try:
        result = await _in_flight
        if result:
            _append_memory_saved_to_agent(agent, result)
    finally:
        _in_flight = None

        trailing = _pending
        _pending = None
        if trailing is not None:
            logger.debug("Running trailing extraction for stashed context")
            asyncio.ensure_future(
                _run_with_trailing(
                    trailing.messages,
                    trailing.provider,
                    memory_dir=trailing.memory_dir,
                    project_root=trailing.project_root,
                    agent=trailing.agent,
                )
            )

    return result


# ── Drain pending extraction ────────────────────────────────────────

async def drain_pending_extraction(timeout: float = 60.0) -> None:
    """Wait for any in-flight extraction to complete.

    Called during graceful shutdown to let the extraction agent finish
    writing memory files before the process exits.  Returns immediately
    if no extraction is in progress.
    """
    if _in_flight is None or _in_flight.done():
        return

    try:
        await asyncio.wait_for(asyncio.shield(_in_flight), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        logger.debug("Drain timed out after %.1fs", timeout)
    except Exception:
        pass


# ── Enhanced memory types ───────────────────────────────────────────

ENHANCED_TYPES_DESCRIPTION = """\
## Memory types

- **preference** — User preferences and working style
  Examples: "Prefers pytest over unittest", "Uses 4-space indent",
  "Likes detailed code comments", "Prefers functional style over OOP"

- **project** — Project-specific facts: architecture, conventions, key files
  Examples: "API routes are in src/api/", "Uses SQLAlchemy with async sessions",
  "Frontend is React 18 with TypeScript", "Deploys via GitHub Actions"

- **decision** — Important decisions and their rationale
  Examples: "Chose Redis over Memcached for caching because of pub/sub support",
  "Decided to use monorepo structure for shared types",
  "Will not use ORMs for the analytics service — raw SQL for performance"

- **correction** — User corrections of AI behavior
  Examples: "Don't suggest print() for debugging — user uses loguru",
  "Always run tests before committing", "Don't modify the Makefile",
  "User corrected: the config file is TOML, not YAML"
"""


def _build_enhanced_extract_prompt(
    new_message_count: int,
    existing_memories: str,
) -> str:
    """Build extraction prompt using the four enhanced memory types."""
    manifest = ""
    if existing_memories:
        manifest = (
            f"\n\n## Existing memory files\n\n{existing_memories}\n\n"
            "Check this list before writing — update an existing file "
            "rather than creating a duplicate."
        )

    return f"""\
You are now acting as the memory extraction subagent. Analyze the most \
recent ~{new_message_count} messages above and extract durable memories.

You MUST only use content from the last ~{new_message_count} messages. \
Do not investigate or verify content further.{manifest}

If the user explicitly asks to remember something, save it as whichever \
type fits best. If they ask to forget something, note that for removal.

{ENHANCED_TYPES_DESCRIPTION}

{WHAT_NOT_TO_SAVE_DETAILED}

## How to save memories

Write each memory as a JSON object with these fields:
- path: filename for the memory (e.g., "user_preferences.md")
- content: full markdown content with frontmatter

Frontmatter format:
{ENHANCED_FRONTMATTER_EXAMPLE}

Respond with a JSON array of memories to save. If nothing worth saving, \
respond with an empty array [].

Example response:
```json
[
  {{
    "path": "user_testing_preference.md",
    "content": "---\\nname: user-testing-preference\\ndescription: User prefers pytest with -v flag\\ntype: preference\\ntags: [coding-style, testing]\\ncreated: {date.today().isoformat()}\\n---\\n\\nUser prefers running tests with `pytest -v` for verbose output."
  }}
]
```"""


# ── Frontmatter helpers ─────────────────────────────────────────────

ENHANCED_FRONTMATTER_EXAMPLE = """\
---
type: preference
tags: [coding-style]
created: 2024-01-01
---
"""

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n",
    re.DOTALL,
)


def parse_memory_frontmatter(content: str) -> dict[str, Any]:
    """Parse YAML-like frontmatter from a memory file.

    Returns a dict with keys like ``type``, ``tags``, ``created``.
    Tags are parsed from ``[tag1, tag2]`` list notation into a Python
    list.  Returns an empty dict if no frontmatter is found.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}

    result: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1]
                result[key] = [
                    t.strip().strip("'\"")
                    for t in inner.split(",")
                    if t.strip()
                ]
            else:
                result[key] = value

    return result


def create_memory_frontmatter(
    memory_type: str,
    *,
    tags: list[str] | None = None,
    created: str = "",
) -> str:
    """Create a standard frontmatter block for a memory file.

    Args:
        memory_type: One of ``preference``, ``project``, ``decision``,
            ``correction`` (or legacy ``user``, ``feedback``, etc.).
        tags: Optional list of tag strings.
        created: ISO date string. Defaults to today.

    Returns the frontmatter block including the ``---`` delimiters.
    """
    if not created:
        created = date.today().isoformat()

    lines = ["---"]
    lines.append(f"type: {memory_type}")
    if tags:
        tag_str = ", ".join(tags)
        lines.append(f"tags: [{tag_str}]")
    lines.append(f"created: {created}")
    lines.append("---")
    return "\n".join(lines)


# ── Turn-based throttle ────────────────────────────────────────────

MIN_TURNS_BETWEEN_EXTRACTIONS = 5

_turns_since_last_extraction: int = 0


def should_throttle_extraction() -> bool:
    """Check whether extraction should be skipped due to turn throttle.

    At least :data:`MIN_TURNS_BETWEEN_EXTRACTIONS` eligible turns must
    pass between extractions.  Call :func:`record_extraction_turn` after
    a successful extraction to reset the counter.
    """
    return _turns_since_last_extraction < MIN_TURNS_BETWEEN_EXTRACTIONS


def increment_turn_counter() -> None:
    """Increment the turn counter (call once per eligible turn)."""
    global _turns_since_last_extraction
    _turns_since_last_extraction += 1


def record_extraction_turn() -> None:
    """Reset the turn counter after a successful extraction."""
    global _turns_since_last_extraction
    _turns_since_last_extraction = 0


# ── Memory saved notification ──────────────────────────────────────

def create_memory_saved_message(
    paths: list[str],
    *,
    verb: str = "Saved",
    source: str = "extract_memories",
) -> Message:
    """Create a system message notifying the AI that memories were saved.

    Injects a ``[Memory saved: <path>]`` line into the conversation so
    the main agent knows a memory was persisted (and can refer to it).
    """
    normalized_verb = verb.strip() or "Saved"
    lowered = normalized_verb.lower()
    if len(paths) == 1:
        text = f"[Memory {lowered}: {paths[0]}]"
    else:
        listing = ", ".join(paths)
        text = f"[Memories {lowered}: {listing}]"

    return system_message(
        text,
        subtype="memory_saved",
        source=source,
        memory_paths=list(paths),
        verb=normalized_verb,
    )


def _append_memory_saved_to_agent(
    agent: Any | None,
    paths: list[str],
    *,
    verb: str = "Saved",
    source: str = "extract_memories",
) -> None:
    if agent is None or not paths:
        return
    message = create_memory_saved_message(paths, verb=verb, source=source)
    append_runtime = getattr(agent, "_append_runtime_system_message", None)
    if callable(append_runtime):
        try:
            append_runtime(message)
            return
        except Exception:
            logger.debug("Failed to append runtime memory_saved message", exc_info=True)

    messages = getattr(agent, "_messages", None)
    if not isinstance(messages, list):
        return
    messages.append(message)
    persist = getattr(agent, "_persist_session_snapshot", None)
    if callable(persist):
        try:
            persist()
        except Exception:
            logger.debug("Failed to persist memory_saved system message", exc_info=True)


# ── WHAT_NOT_TO_SAVE (detailed) ────────────────────────────────────

WHAT_NOT_TO_SAVE_DETAILED = """\
## What NOT to save

- **Transient debugging steps** — one-off print statements, temporary
  breakpoints, debugging commands that won't be reused
- **File contents verbatim** — don't copy code into memory; reference
  file paths instead
- **Code snippets** — memory is for *knowledge*, not code storage;
  link to files
- **Information already in CLAUDE.md or project docs** — don't duplicate
  what the project README, AGENTS.md, or other config files already say
- **Obvious facts** — the user's OS, the project's primary language, or
  anything inferable from the project structure
- **Secrets, API keys, or credentials** — never persist sensitive data
- **Single-conversation ephemera** — information with no future value
  beyond the current chat session
"""
