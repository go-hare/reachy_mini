"""Memory directory — persistent file-based memory system.

Ported from Claude Code's ``memdir/`` subsystem:
- Scans a directory for .md memory files with YAML frontmatter
- Selects relevant memories per query via a side-query classifier
- Formats memory manifests for injection into system prompts
- Manages memory paths (auto-memory, team memory)

Memory types: user, feedback, project, reference
Frontmatter fields: name, description, type
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..paths import mini_agent_path

if TYPE_CHECKING:
    from ..providers import BaseProvider

logger = logging.getLogger(__name__)

# ── Memory types ────────────────────────────────────────────────────

MEMORY_TYPES = ("user", "feedback", "project", "reference")

MEMORY_FRONTMATTER_EXAMPLE = """\
---
name: descriptive-kebab-case-name
description: One-line description of what this memory contains
type: user | feedback | project | reference
---
"""

TYPES_DESCRIPTION = """\
## Memory types

- **user**: Personal preferences, communication style, workflow habits
- **feedback**: Corrections the user made, things to avoid or repeat
- **project**: Architecture decisions, key patterns, important files
- **reference**: API docs, tool gotchas, environment-specific notes
"""

WHAT_NOT_TO_SAVE = """\
## What NOT to save
- Trivial or transient information
- Information already in project docs (CLAUDE.md, README, etc.)
- Secrets, API keys, or credentials
- Large code blocks (link to files instead)
- Information specific to a single conversation with no future value
"""

# ── Frontmatter parsing ────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n",
    re.DOTALL,
)


def parse_frontmatter(content: str) -> dict[str, str]:
    """Parse YAML-like frontmatter from the start of a markdown file."""
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}

    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


# ── Memory header scanning ──────────────────────────────────────────

@dataclass(slots=True)
class MemoryHeader:
    """Metadata extracted from a memory file's frontmatter."""
    filename: str
    filepath: str
    mtime: float
    description: str | None = None
    memory_type: str | None = None


MAX_MEMORY_FILES = 200
FRONTMATTER_MAX_CHARS = 2000


async def scan_memory_files(
    memory_dir: str,
    *,
    max_files: int = MAX_MEMORY_FILES,
) -> list[MemoryHeader]:
    """Scan a memory directory for .md files, read frontmatter, return headers.

    Returns newest-first, capped at max_files. Excludes MEMORY.md index files.
    """
    mem_path = Path(memory_dir)
    if not mem_path.is_dir():
        return []

    try:
        md_files = sorted(
            mem_path.rglob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []

    headers: list[MemoryHeader] = []
    for fp in md_files[:max_files * 2]:
        if fp.name == "MEMORY.md":
            continue
        try:
            content = fp.read_text(encoding="utf-8")[:FRONTMATTER_MAX_CHARS]
            fm = parse_frontmatter(content)
            headers.append(MemoryHeader(
                filename=str(fp.relative_to(mem_path)),
                filepath=str(fp),
                mtime=fp.stat().st_mtime,
                description=fm.get("description"),
                memory_type=fm.get("type"),
            ))
        except (OSError, UnicodeDecodeError):
            continue

        if len(headers) >= max_files:
            break

    return headers


def format_memory_manifest(memories: list[MemoryHeader]) -> str:
    """Format memory headers as a text manifest for prompts."""
    lines: list[str] = []
    for m in memories:
        tag = f"[{m.memory_type}] " if m.memory_type else ""
        desc = f": {m.description}" if m.description else ""
        lines.append(f"- {tag}{m.filename}{desc}")
    return "\n".join(lines)


# ── Relevance selection ─────────────────────────────────────────────

SELECT_MEMORIES_SYSTEM = """\
You are selecting memories that will be useful to an AI assistant as it \
processes a user's query. You will be given the query and a list of \
available memory files with descriptions.

Return a JSON object with a "selected_memories" array of filenames \
(up to 5). Only include memories you are CERTAIN will be helpful.

If unsure, return an empty array. Be selective and discerning.
"""


@dataclass(slots=True)
class RelevantMemory:
    """A memory selected as relevant to a query."""
    path: str
    mtime: float


async def find_relevant_memories(
    query: str,
    memory_dir: str,
    provider: BaseProvider,
    *,
    max_results: int = 5,
    already_surfaced: set[str] | None = None,
    recent_tools: list[str] | None = None,
) -> list[RelevantMemory]:
    """Find memory files relevant to a query using a side-query classifier.

    Scans memory files, asks the model to select the most relevant ones
    (up to 5), and returns their paths. Mirrors Claude Code's
    ``findRelevantMemories.ts``.
    """
    memories = await scan_memory_files(memory_dir)
    if not memories:
        return []

    # Filter already-surfaced
    if already_surfaced:
        memories = [m for m in memories if m.filepath not in already_surfaced]
    if not memories:
        return []

    selected = await _select_relevant(
        query, memories, provider,
        recent_tools=recent_tools or [],
    )

    by_filename = {m.filename: m for m in memories}
    results: list[RelevantMemory] = []
    for filename in selected[:max_results]:
        mem = by_filename.get(filename)
        if mem:
            results.append(RelevantMemory(path=mem.filepath, mtime=mem.mtime))

    return results


async def _select_relevant(
    query: str,
    memories: list[MemoryHeader],
    provider: BaseProvider,
    *,
    recent_tools: list[str],
) -> list[str]:
    """Use a side query to select relevant memories from the manifest."""
    import json
    from ..delegation.fork import run_forked_side_query

    manifest = format_memory_manifest(memories)
    valid_names = {m.filename for m in memories}

    tools_section = ""
    if recent_tools:
        tools_section = f"\n\nRecently used tools: {', '.join(recent_tools)}"

    prompt = f"Query: {query}\n\nAvailable memories:\n{manifest}{tools_section}"

    try:
        result = await run_forked_side_query(
            provider=provider,
            parent_messages=[],
            system_prompt=SELECT_MEMORIES_SYSTEM,
            prompt=prompt,
            max_tokens=256,
            temperature=0.0,
            query_source="memdir_relevance",
        )

        text = result.strip()
        # Try to parse JSON
        if "{" in text:
            start = text.index("{")
            end = text.rindex("}") + 1
            parsed = json.loads(text[start:end])
            selected = parsed.get("selected_memories", [])
            return [f for f in selected if f in valid_names]

        return []

    except Exception as exc:
        logger.warning("Memory relevance selection failed: %s", exc)
        return []


# ── Memory reading ──────────────────────────────────────────────────

async def read_memory_file(path: str) -> str | None:
    """Read a memory file's content."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


async def load_relevant_memory_content(
    memories: list[RelevantMemory],
    *,
    max_total_tokens: int = 10_000,
) -> str:
    """Load and concatenate relevant memory file contents, within budget."""
    parts: list[str] = []
    total_chars = 0
    char_budget = max_total_tokens * 4

    for mem in memories:
        content = await read_memory_file(mem.path)
        if content and total_chars + len(content) <= char_budget:
            filename = Path(mem.path).name
            parts.append(f"### Memory: {filename}\n{content}")
            total_chars += len(content)

    return "\n\n".join(parts) if parts else ""


# ── Path management ─────────────────────────────────────────────────

def get_memory_dir(project_root: str = "") -> str:
    """Get the auto-memory directory path."""
    base = os.environ.get("MEMORY_DIR", "")
    if base:
        return base
    home = mini_agent_path("memory")
    if project_root:
        from hashlib import md5
        slug = md5(project_root.encode()).hexdigest()[:12]
        return str(home / slug)
    return str(home)


def is_memory_path(filepath: str, memory_dir: str = "") -> bool:
    """Check if a path is within the memory directory."""
    if not memory_dir:
        memory_dir = get_memory_dir()
    return os.path.normpath(filepath).startswith(os.path.normpath(memory_dir))
