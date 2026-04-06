"""Skills framework — load markdown skill files and inject into system prompt.

Ported from Claude Code's ``skills/loadSkillsDir.ts`` with full YAML
frontmatter support:

**Frontmatter fields** (between ``---`` delimiters):
- ``description`` — one-line description shown in skill listings
- ``allowed-tools`` — restrict which tools the skill can use
- ``model`` — override model for this skill (e.g. ``sonnet``, ``haiku``)
- ``paths`` — gitignore-style patterns for conditional activation
- ``shell`` — shell command to run for context gathering before prompt
- ``hooks`` — pre/post hooks (``pre-tool``, ``post-tool``)
- ``effort`` — effort level hint (``low``, ``medium``, ``high``)
- ``context`` — extra context to inject (``file``, ``git``)
- ``agent`` — agent type override
- ``user-invocable`` — whether users can directly call via ``/skill``
- ``arguments`` — named argument definitions for ``$ARGUMENTS`` substitution
- ``tags`` — comma-separated tags for context matching
- ``priority`` — numeric priority for ordering (higher = more relevant)

**Conditional activation via paths**:
Skills with ``paths:`` only activate when touched files match the patterns.
Uses gitignore-style matching (``*.py``, ``src/**/*.ts``, ``!tests/``).

**Variable substitution in prompts**:
- ``${SKILL_DIR}`` — directory containing the skill file
- ``${SESSION_ID}`` — current session ID
- ``$ARGUMENTS`` — full argument string
- ``$ARGUMENTS[0]``, ``$0``, ``$1`` — positional arguments

Usage::

    loader = SkillLoader(skill_dirs=[Path(".mini_agent/skills")])
    skills = loader.discover()
    source = SkillAttachmentSource(loader)
    agent.attachment_collector.add_source(source)
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Frontmatter parsing ────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n",
    re.DOTALL,
)


@dataclass
class SkillFrontmatter:
    """Parsed frontmatter from a SKILL.md file."""
    description: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""
    paths: list[str] = field(default_factory=list)
    shell: str = ""
    hooks: dict[str, str] = field(default_factory=dict)
    effort: str = ""
    context: list[str] = field(default_factory=list)
    agent: str = ""
    user_invocable: bool = True
    arguments: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    priority: int = 0


def parse_skill_frontmatter(text: str) -> tuple[SkillFrontmatter, str]:
    """Parse YAML frontmatter from a skill file.

    Returns (frontmatter, body) where body is the content after
    the frontmatter block. Handles both YAML-style ``key: value``
    and comma-separated lists.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return SkillFrontmatter(), text

    raw = match.group(1)
    body = text[match.end():]
    fm = SkillFrontmatter()

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue

        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()

        if key == "description":
            fm.description = value
        elif key in ("allowed-tools", "allowed_tools"):
            fm.allowed_tools = _split_csv(value)
        elif key == "model":
            fm.model = value
        elif key == "paths":
            fm.paths = _split_paths(value)
        elif key == "shell":
            fm.shell = value
        elif key == "effort":
            fm.effort = value.lower()
        elif key == "context":
            fm.context = _split_csv(value)
        elif key == "agent":
            fm.agent = value
        elif key in ("user-invocable", "user_invocable"):
            fm.user_invocable = value.lower() not in ("false", "no", "0")
        elif key == "arguments":
            fm.arguments = _split_csv(value)
        elif key == "tags":
            fm.tags = _split_csv(value)
        elif key == "priority":
            try:
                fm.priority = int(value)
            except ValueError:
                pass
        elif key.startswith("hook-") or key.startswith("hooks-"):
            hook_name = key.split("-", 1)[1] if "-" in key else key
            fm.hooks[hook_name] = value

    return fm, body


def _split_csv(value: str) -> list[str]:
    """Split comma-separated value, respecting braces."""
    if not value:
        return []
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in value:
        if char == "{":
            depth += 1
            current.append(char)
        elif char == "}":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            token = "".join(current).strip()
            if token:
                parts.append(token)
            current = []
        else:
            current.append(char)
    token = "".join(current).strip()
    if token:
        parts.append(token)
    return parts


def _split_paths(value: str) -> list[str]:
    """Split and normalize path patterns from frontmatter.

    Supports comma-separated and brace-expanded patterns.
    Strips trailing ``/**`` suffixes for directory matching.
    """
    raw = _split_csv(value)
    paths: list[str] = []
    for p in raw:
        # Expand braces: *.{ts,tsx} → ["*.ts", "*.tsx"]
        expanded = _expand_braces(p)
        for ep in expanded:
            ep = ep.strip()
            if ep and ep != "**":
                paths.append(ep)
    return paths


def _expand_braces(pattern: str) -> list[str]:
    """Expand simple brace patterns: ``*.{a,b}`` → ``["*.a", "*.b"]``."""
    match = re.search(r"\{([^{}]+)\}", pattern)
    if not match:
        return [pattern]

    prefix = pattern[:match.start()]
    suffix = pattern[match.end():]
    alternatives = match.group(1).split(",")

    results: list[str] = []
    for alt in alternatives:
        expanded = _expand_braces(f"{prefix}{alt.strip()}{suffix}")
        results.extend(expanded)
    return results


# ── Variable substitution ───────────────────────────────────────────

def substitute_variables(
    text: str,
    *,
    skill_dir: str = "",
    session_id: str = "",
    arguments: str = "",
    named_args: list[str] | None = None,
) -> str:
    """Replace skill variables in prompt text.

    Variables:
    - ``${SKILL_DIR}`` — skill file's parent directory
    - ``${SESSION_ID}`` — current session ID
    - ``$ARGUMENTS`` — full argument string
    - ``$ARGUMENTS[N]`` or ``$N`` — positional arguments
    - ``$name`` — named arguments (from frontmatter ``arguments:``)
    """
    result = text
    result = result.replace("${SKILL_DIR}", skill_dir)
    result = result.replace("${CLAUDE_SKILL_DIR}", skill_dir)
    result = result.replace("${SESSION_ID}", session_id)
    result = result.replace("${CLAUDE_SESSION_ID}", session_id)

    # Positional arguments
    arg_parts = arguments.split() if arguments else []
    result = result.replace("$ARGUMENTS", arguments)

    for i, part in enumerate(arg_parts):
        result = result.replace(f"$ARGUMENTS[{i}]", part)
        result = result.replace(f"${i}", part)

    # Named arguments
    if named_args:
        for i, name in enumerate(named_args):
            if i < len(arg_parts):
                result = result.replace(f"${name}", arg_parts[i])

    return result


# ── Conditional path matching ───────────────────────────────────────

def matches_path_patterns(
    filepath: str,
    patterns: list[str],
) -> bool:
    """Check if a file path matches any of the skill's path patterns.

    Uses gitignore-style matching:
    - ``*.py`` matches any Python file
    - ``src/**/*.ts`` matches TypeScript files under src/
    - ``!tests/`` negates the pattern
    """
    if not patterns:
        return True  # no patterns = always active

    filepath = filepath.replace("\\", "/")
    matched = False

    for pattern in patterns:
        negate = pattern.startswith("!")
        pat = pattern[1:] if negate else pattern

        pat = pat.rstrip("/")
        if "**" in pat:
            pat = pat.replace("**/", "*")

        if fnmatch.fnmatch(filepath, pat) or fnmatch.fnmatch(
            os.path.basename(filepath), pat
        ):
            matched = not negate

    return matched


def activate_conditional_skills(
    skills: list[Skill],
    touched_files: list[str],
) -> list[Skill]:
    """Filter skills to only those whose path patterns match touched files."""
    activated: list[Skill] = []
    for skill in skills:
        if not skill.frontmatter.paths:
            activated.append(skill)
            continue
        for fp in touched_files:
            if matches_path_patterns(fp, skill.frontmatter.paths):
                activated.append(skill)
                break
    return activated


# ── Skill dataclass ─────────────────────────────────────────────────

@dataclass
class Skill:
    """A loaded skill definition with full frontmatter support."""
    name: str
    path: Path
    content: str
    body: str = ""
    title: str = ""
    tags: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    priority: int = 0
    frontmatter: SkillFrontmatter = field(default_factory=SkillFrontmatter)
    is_conditional: bool = False

    @property
    def token_estimate(self) -> int:
        return len(self.content) // 4

    @property
    def description(self) -> str:
        return self.frontmatter.description or self.title

    @property
    def is_user_invocable(self) -> bool:
        return self.frontmatter.user_invocable

    def render_prompt(
        self,
        *,
        skill_dir: str = "",
        session_id: str = "",
        arguments: str = "",
    ) -> str:
        """Render the skill's prompt with variable substitution."""
        text = self.body or self.content
        return substitute_variables(
            text,
            skill_dir=skill_dir or str(self.path.parent),
            session_id=session_id,
            arguments=arguments,
            named_args=self.frontmatter.arguments,
        )


# ── Skill loader ────────────────────────────────────────────────────

class SkillLoader:
    """Discover and load skill definitions from directories.

    Supports both single-file skills (``skill.md``) and directory
    skills (``skill-name/SKILL.md``).
    """

    def __init__(self, skill_dirs: list[Path] | None = None) -> None:
        self._dirs = list(skill_dirs or [])
        self._skills: dict[str, Skill] = {}
        self._conditional: list[Skill] = []
        self._unconditional: list[Skill] = []

    @property
    def skills(self) -> dict[str, Skill]:
        return dict(self._skills)

    @property
    def conditional_skills(self) -> list[Skill]:
        return list(self._conditional)

    @property
    def unconditional_skills(self) -> list[Skill]:
        return list(self._unconditional)

    def add_directory(self, path: Path) -> None:
        if path not in self._dirs:
            self._dirs.append(path)

    def discover(self) -> list[Skill]:
        """Scan directories for skill files and parse them.

        Discovers both ``*.md`` files and ``*/SKILL.md`` directory skills.
        Deduplicates by resolved path.
        """
        found: list[Skill] = []
        seen_paths: set[str] = set()

        for d in self._dirs:
            if not d.is_dir():
                continue

            # Directory skills: skill-name/SKILL.md
            for subdir in sorted(d.iterdir()):
                if not subdir.is_dir():
                    continue
                skill_md = subdir / "SKILL.md"
                if skill_md.exists():
                    resolved = str(skill_md.resolve())
                    if resolved not in seen_paths:
                        seen_paths.add(resolved)
                        try:
                            skill = self._parse_skill(skill_md, name=subdir.name)
                            self._register(skill)
                            found.append(skill)
                        except Exception as exc:
                            logger.warning("Failed to parse skill %s: %s", skill_md, exc)

            # Single-file skills: *.md (excluding SKILL.md already handled)
            for md_path in sorted(d.glob("*.md")):
                if md_path.name == "SKILL.md":
                    continue
                resolved = str(md_path.resolve())
                if resolved not in seen_paths:
                    seen_paths.add(resolved)
                    try:
                        skill = self._parse_skill(md_path)
                        self._register(skill)
                        found.append(skill)
                    except Exception as exc:
                        logger.warning("Failed to parse skill %s: %s", md_path, exc)

            # Recursive single-file skills in subdirectories
            for md_path in sorted(d.rglob("*.md")):
                if md_path.name == "SKILL.md":
                    continue
                if md_path.parent == d:
                    continue  # already handled above
                resolved = str(md_path.resolve())
                if resolved not in seen_paths:
                    seen_paths.add(resolved)
                    try:
                        skill = self._parse_skill(md_path)
                        self._register(skill)
                        found.append(skill)
                    except Exception as exc:
                        logger.warning("Failed to parse skill %s: %s", md_path, exc)

        return found

    def _register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill
        if skill.is_conditional:
            self._conditional.append(skill)
        else:
            self._unconditional.append(skill)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def match(
        self,
        context: str,
        *,
        max_skills: int = 5,
        touched_files: list[str] | None = None,
    ) -> list[Skill]:
        """Select skills relevant to the given context text.

        If ``touched_files`` is provided, conditional skills are filtered
        by path pattern matching first.
        """
        candidates = list(self._unconditional)
        if touched_files:
            candidates.extend(
                activate_conditional_skills(self._conditional, touched_files)
            )
        else:
            candidates.extend(self._conditional)

        context_lower = context.lower()
        scored: list[tuple[int, Skill]] = []
        for skill in candidates:
            score = skill.priority
            for tag in skill.tags:
                if tag.lower() in context_lower:
                    score += 10
            if skill.name.lower() in context_lower:
                score += 5
            if skill.frontmatter.description:
                desc_words = skill.frontmatter.description.lower().split()
                for word in desc_words:
                    if len(word) > 3 and word in context_lower:
                        score += 2
            if score > 0:
                scored.append((score, skill))
        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:max_skills]]

    def render(self, skills: list[Skill], **kwargs: Any) -> str:
        """Render selected skills as a combined text block."""
        if not skills:
            return ""
        parts: list[str] = []
        for skill in skills:
            header = f"## Skill: {skill.title or skill.name}"
            prompt = skill.render_prompt(**kwargs)
            parts.append(f"{header}\n\n{prompt}")
        return "\n\n---\n\n".join(parts)

    def _parse_skill(self, path: Path, name: str = "") -> Skill:
        """Parse a markdown skill file with full frontmatter support."""
        text = path.read_text(encoding="utf-8")
        skill_name = name or path.stem

        # Parse frontmatter
        fm, body = parse_skill_frontmatter(text)

        # Extract title from first heading if not in frontmatter
        title = fm.description
        title_match = re.search(r"^#\s+(.+)$", body or text, re.MULTILINE)
        if title_match and not title:
            title = title_match.group(1).strip()

        # Merge legacy inline metadata with frontmatter
        if not fm.tags:
            tags_match = re.search(r"tags:\s*(.+)$", body, re.MULTILINE | re.IGNORECASE)
            if tags_match:
                fm.tags = [t.strip() for t in tags_match.group(1).split(",")]

        if not fm.allowed_tools:
            tools_match = re.search(r"tools:\s*(.+)$", body, re.MULTILINE | re.IGNORECASE)
            if tools_match:
                fm.allowed_tools = [t.strip() for t in tools_match.group(1).split(",")]

        if fm.priority == 0:
            prio_match = re.search(r"priority:\s*(\d+)", body, re.IGNORECASE)
            if prio_match:
                fm.priority = int(prio_match.group(1))

        return Skill(
            name=skill_name,
            path=path,
            content=text,
            body=body,
            title=title,
            tags=fm.tags,
            tools=fm.allowed_tools,
            priority=fm.priority,
            frontmatter=fm,
            is_conditional=bool(fm.paths),
        )


# ── Skill attachment source ────────────────────────────────────────

class SkillAttachmentSource:
    """AttachmentSource that injects relevant skills as context.

    Register with ``agent.attachment_collector.add_source(source)``.
    """

    def __init__(self, loader: SkillLoader, *, max_skills: int = 3) -> None:
        self._loader = loader
        self._max_skills = max_skills

    async def get_attachments(self, context: dict[str, Any]) -> list[Any]:
        from ..attachments import Attachment

        user_text = context.get("user_text", "")
        if not user_text:
            return []

        touched = context.get("touched_files", [])
        matched = self._loader.match(
            user_text,
            max_skills=self._max_skills,
            touched_files=touched,
        )
        if not matched:
            return []

        render_kwargs: dict[str, Any] = {}
        if "session_id" in context:
            render_kwargs["session_id"] = context["session_id"]
        if "arguments" in context:
            render_kwargs["arguments"] = context["arguments"]

        rendered = self._loader.render(matched, **render_kwargs)
        return [Attachment(
            type="skill",
            content=rendered,
            metadata={"skill_names": [s.name for s in matched]},
        )]


# ── Discovery helpers ───────────────────────────────────────────────

def _register_mcp_bridge() -> None:
    """Register skill builders into the MCP skill bridge at import time."""
    try:
        from ..mcp.skill_bridge import MCPSkillBuilders, register_mcp_skill_builders
        from ..commands import command_from_mcp_skill

        register_mcp_skill_builders(MCPSkillBuilders(
            parse_frontmatter=parse_skill_frontmatter,
            create_command=command_from_mcp_skill,
        ))
    except Exception:
        pass


_register_mcp_bridge()


def discover_skill_dirs_for_path(
    filepath: str,
    *,
    stop_at: str = "",
) -> list[Path]:
    """Walk up from a file path discovering .mini_agent/skills directories.

    Mirrors Claude Code's ``discoverSkillDirsForPaths()`` — finds skill
    directories that are contextually relevant to specific files.
    """
    dirs: list[Path] = []
    current = Path(filepath).resolve()
    if current.is_file():
        current = current.parent

    stop = Path(stop_at).resolve() if stop_at else Path.home()

    while current != current.parent:
        skills_dir = current / ".mini_agent" / "skills"
        if skills_dir.is_dir():
            dirs.append(skills_dir)
        if current == stop:
            break
        current = current.parent

    return dirs
