"""GlobTool — find files matching a glob pattern."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any

from ..tool import Tool, ToolUseContext, resolve_path

_MAX_RESULTS = 2000

_DEFAULT_IGNORE_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv"}


class GlobTool(Tool):
    name = "Glob"
    description = (
        "Find files matching a glob pattern. Searches recursively from the "
        "given directory (or cwd). Returns matching file paths."
    )
    instructions = """\
Fast file pattern matching tool that works with any codebase size.

- Supports glob patterns like "**/*.py" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- Respects .gitignore and .mini_agent_ignore by default
- Supports sorting by modification time, name, or size
- Can filter out files exceeding a maximum size
- When doing an open-ended search that may require multiple rounds of \
globbing and grepping, use the Agent tool instead\
"""
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern (e.g. '**/*.py', '*.json')",
                },
                "path": {
                    "type": "string",
                    "description": "Base directory to search in (default: cwd)",
                },
                "include_ignored": {
                    "type": "boolean",
                    "description": (
                        "Include files that would be ignored by .gitignore / "
                        ".mini_agent_ignore (default: false)"
                    ),
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["modified", "name", "size"],
                    "description": (
                        "Sort results: 'modified' (newest first, default), "
                        "'name' (alphabetical), 'size' (largest first)"
                    ),
                },
                "max_file_size": {
                    "type": "integer",
                    "description": (
                        "Skip files larger than this many bytes. "
                        "Useful for excluding binary/data files."
                    ),
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        pattern: str = kwargs["pattern"]
        base = resolve_path(kwargs.get("path", "."), context)
        include_ignored: bool = kwargs.get("include_ignored", False)
        sort_by: str = kwargs.get("sort_by", "modified")
        max_file_size: int | None = kwargs.get("max_file_size")

        if not base.exists():
            return f"Error: Directory not found: {base}"

        ignore_patterns: list[str] = []
        if not include_ignored:
            ignore_patterns = _load_gitignore(base) + _load_gitignore(base, filename=".mini_agent_ignore")

        try:
            raw_matches: list[Path] = []
            for p in base.glob(pattern):
                if not include_ignored and _should_ignore(p, base, ignore_patterns):
                    continue
                if max_file_size is not None and p.is_file():
                    try:
                        if p.stat().st_size > max_file_size:
                            continue
                    except OSError:
                        continue
                raw_matches.append(p)

            raw_matches = _sort_paths(raw_matches, sort_by)

            matches: list[str] = []
            for p in raw_matches:
                matches.append(str(p.relative_to(base)))
                if len(matches) >= _MAX_RESULTS:
                    matches.append(f"... (truncated at {_MAX_RESULTS} results)")
                    break

            if not matches:
                return f"No files matching '{pattern}' in {base}"

            return "\n".join(matches)

        except Exception as exc:
            return f"Error: {exc}"


# ── Gitignore / ignore-file support ──────────────────────────────────


def _load_gitignore(dir_path: Path, *, filename: str = ".gitignore") -> list[str]:
    """Parse a .gitignore-style file and return a list of patterns.

    Walks up from *dir_path* to the filesystem root so that parent
    .gitignore files are also respected.
    """
    patterns: list[str] = []
    current = dir_path
    visited: set[Path] = set()
    while current not in visited:
        visited.add(current)
        ignore_file = current / filename
        if ignore_file.is_file():
            try:
                for line in ignore_file.read_text(errors="replace").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line)
            except OSError:
                pass
        parent = current.parent
        if parent == current:
            break
        current = parent
    return patterns


def _should_ignore(path: Path, base: Path, patterns: list[str]) -> bool:
    """Return True if *path* matches any gitignore-style *patterns*."""
    if not patterns:
        return False

    try:
        rel = path.relative_to(base)
    except ValueError:
        return False

    rel_str = str(rel).replace(os.sep, "/")
    name = path.name

    for part in rel.parts:
        if part in _DEFAULT_IGNORE_DIRS:
            return True

    for pat in patterns:
        pat_clean = pat.rstrip("/")
        if fnmatch.fnmatch(name, pat_clean):
            return True
        if fnmatch.fnmatch(rel_str, pat_clean):
            return True
        if fnmatch.fnmatch(rel_str, pat_clean + "/**"):
            return True
        if "/" not in pat_clean and fnmatch.fnmatch(rel_str, "**/" + pat_clean):
            return True

    return False


# ── Sorting helpers ──────────────────────────────────────────────────


def _sort_paths(paths: list[Path], sort_by: str) -> list[Path]:
    """Sort a list of paths by the given strategy."""
    if sort_by == "name":
        return sorted(paths, key=lambda p: str(p).lower())
    if sort_by == "size":
        return sorted(paths, key=lambda p: _safe_stat_size(p), reverse=True)
    # default: modified (newest first)
    return sorted(paths, key=lambda p: _safe_mtime(p), reverse=True)


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _safe_stat_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0
