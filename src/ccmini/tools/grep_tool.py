"""GrepTool — search file contents with regex."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..tool import Tool, ToolUseContext, resolve_path

_MAX_MATCHES = 500
_MAX_LINE_LEN = 500

# File-type extension map (mirrors ripgrep --type)
_FILE_TYPE_MAP: dict[str, list[str]] = {
    "py": ["*.py", "*.pyi"],
    "js": ["*.js", "*.mjs", "*.cjs"],
    "ts": ["*.ts", "*.tsx", "*.mts", "*.cts"],
    "tsx": ["*.tsx"],
    "jsx": ["*.jsx"],
    "rust": ["*.rs"],
    "go": ["*.go"],
    "java": ["*.java"],
    "c": ["*.c", "*.h"],
    "cpp": ["*.cpp", "*.cxx", "*.cc", "*.hpp", "*.hxx", "*.h"],
    "cs": ["*.cs"],
    "rb": ["*.rb"],
    "php": ["*.php"],
    "swift": ["*.swift"],
    "kt": ["*.kt", "*.kts"],
    "scala": ["*.scala"],
    "html": ["*.html", "*.htm"],
    "css": ["*.css"],
    "scss": ["*.scss"],
    "json": ["*.json"],
    "yaml": ["*.yaml", "*.yml"],
    "toml": ["*.toml"],
    "xml": ["*.xml"],
    "md": ["*.md", "*.mdx"],
    "sql": ["*.sql"],
    "sh": ["*.sh", "*.bash"],
    "ps1": ["*.ps1", "*.psm1"],
    "dockerfile": ["Dockerfile", "Dockerfile.*"],
}


class GrepTool(Tool):
    name = "Grep"
    description = (
        "Search file contents using a regex pattern. Searches all files in "
        "the directory recursively. Returns matching lines with file paths "
        "and line numbers. Prefers ripgrep (rg) for speed when available."
    )
    instructions = """\
A powerful search tool built on ripgrep (with Python fallback).

Usage:
- ALWAYS use this grep tool for search tasks. NEVER invoke grep or rg as \
a bash command. This tool has been optimized for correct permissions and \
access.
- Supports full regex syntax (e.g. "log.*Error", "function\\s+\\w+")
- Filter files with the include parameter (e.g. "*.py", "*.tsx") or \
type parameter (e.g. "py", "js", "rust")
- Output modes: "content" shows matching lines (default), \
"files_with_matches" shows only file paths, "count" shows match counts
- Pattern syntax: literal braces need escaping (use interface\\{\\} to \
find interface{} in Go code)
- Multiline matching: set multiline=true for patterns that span lines
- Results are capped for performance. If truncated, narrow the search \
with a more specific pattern or include filter.\
"""
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search (default: cwd)",
                },
                "include": {
                    "type": "string",
                    "description": "Glob filter for filenames (e.g. '*.py')",
                },
                "output_mode": {
                    "type": "string",
                    "description": (
                        'Output mode: "content" (default) shows matching lines, '
                        '"files_with_matches" shows file paths only, '
                        '"count" shows match counts per file'
                    ),
                    "enum": ["content", "files_with_matches", "count"],
                },
                "file_type": {
                    "type": "string",
                    "description": (
                        "File type filter (e.g. 'py', 'js', 'ts', 'rust'). "
                        "More efficient than include for standard file types."
                    ),
                },
                "multiline": {
                    "type": "boolean",
                    "description": (
                        "Enable multiline mode where '.' matches newlines "
                        "and patterns can span lines (default: false)"
                    ),
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case insensitive search (default: false)",
                },
                "max_results": {
                    "type": "integer",
                    "description": f"Maximum matches to return (default: {_MAX_MATCHES})",
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Lines of context before and after each match (content mode only)",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        pattern_str: str = kwargs["pattern"]
        search_path = resolve_path(kwargs.get("path", "."), context)
        include: str | None = kwargs.get("include")
        output_mode: str = kwargs.get("output_mode", "content")
        file_type: str | None = kwargs.get("file_type")
        multiline: bool = kwargs.get("multiline", False)
        case_insensitive: bool = kwargs.get("case_insensitive", False)
        max_results: int = kwargs.get("max_results", _MAX_MATCHES)
        context_lines: int | None = kwargs.get("context_lines")

        if not search_path.exists():
            return f"Error: Path not found: {search_path}"

        # Resolve include from file_type if include not provided
        effective_include = include
        if effective_include is None and file_type:
            exts = _FILE_TYPE_MAP.get(file_type.lower())
            if exts:
                effective_include = exts[0] if len(exts) == 1 else None

        # Prefer ripgrep for performance
        if _has_ripgrep():
            return _use_ripgrep(
                pattern_str,
                search_path,
                include=effective_include,
                file_type=file_type,
                output_mode=output_mode,
                multiline=multiline,
                case_insensitive=case_insensitive,
                max_results=max_results,
                context_lines=context_lines,
            )

        # Python fallback
        return _python_grep(
            pattern_str,
            search_path,
            include=effective_include,
            file_type=file_type,
            output_mode=output_mode,
            multiline=multiline,
            case_insensitive=case_insensitive,
            max_results=max_results,
            context_lines=context_lines,
        )


def _iter_files(
    path: Path, include: str | None, file_type: str | None = None,
) -> list[Path]:
    if path.is_file():
        return [path]

    # Build glob patterns from file_type
    type_globs: list[str] | None = None
    if file_type:
        type_globs = _FILE_TYPE_MAP.get(file_type.lower())

    if include:
        return sorted(path.rglob(include))

    if type_globs:
        results: list[Path] = []
        for pattern in type_globs:
            results.extend(path.rglob(pattern))
        return sorted(set(results))

    result: list[Path] = []
    _SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox"}
    for item in sorted(path.rglob("*")):
        if any(part in _SKIP for part in item.parts):
            continue
        if item.is_file():
            result.append(item)
    return result


def _safe_relative(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


# -----------------------------------------------------------------------
# Feature: Ripgrep backend
# -----------------------------------------------------------------------

_ripgrep_available: bool | None = None


def _has_ripgrep() -> bool:
    """Check if ripgrep (rg) is installed and available."""
    global _ripgrep_available
    if _ripgrep_available is None:
        _ripgrep_available = shutil.which("rg") is not None
    return _ripgrep_available


def _use_ripgrep(
    pattern: str,
    search_path: Path,
    *,
    include: str | None,
    file_type: str | None,
    output_mode: str,
    multiline: bool,
    case_insensitive: bool,
    max_results: int,
    context_lines: int | None,
) -> str:
    """Run ripgrep and return formatted results."""
    args: list[str] = ["rg", "--hidden", "--max-columns", "500"]

    # Exclude noisy directories
    for skip in (".git", "__pycache__", "node_modules", ".venv", "venv", ".tox"):
        args.extend(["--glob", f"!{skip}"])

    if multiline:
        args.extend(["-U", "--multiline-dotall"])

    if case_insensitive:
        args.append("-i")

    # Output mode
    if output_mode == "files_with_matches":
        args.append("-l")
    elif output_mode == "count":
        args.append("-c")
    else:
        args.append("-n")
        if context_lines is not None:
            args.extend(["-C", str(context_lines)])

    # Result cap via ripgrep's --max-count (per-file); we also limit total
    args.extend(["--max-count", str(max(max_results, 50))])

    # File type filter
    if file_type:
        rg_type = file_type.lower()
        # rg has built-in types; pass through directly
        args.extend(["--type", rg_type])
    elif include:
        args.extend(["--glob", include])

    # Pattern (use -e for patterns starting with -)
    if pattern.startswith("-"):
        args.extend(["-e", pattern])
    else:
        args.append(pattern)

    args.append(str(search_path))

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "Error: Search timed out after 60s. Try a narrower pattern or path."
    except (FileNotFoundError, PermissionError, OSError):
        # rg disappeared; fall back
        global _ripgrep_available
        _ripgrep_available = False
        return _python_grep(
            pattern, search_path,
            include=include, file_type=file_type,
            output_mode=output_mode, multiline=multiline,
            case_insensitive=case_insensitive,
            max_results=max_results, context_lines=context_lines,
        )

    output = result.stdout.strip()
    if not output and result.returncode == 1:
        return f"No matches for '{pattern}'"
    if result.returncode >= 2 and result.stderr:
        return f"Error: ripgrep: {result.stderr.strip()}"

    lines = output.splitlines()

    # Apply result cap
    capped = False
    if len(lines) > max_results:
        lines = lines[:max_results]
        capped = True

    # Convert absolute paths to relative for cleaner output
    search_str = str(search_path)
    cleaned: list[str] = []
    for line in lines:
        if line.startswith(search_str):
            line = line[len(search_str):].lstrip("/\\")
        cleaned.append(line)

    result_text = "\n".join(cleaned)

    if output_mode == "count":
        total = _sum_rg_counts(cleaned)
        result_text += f"\n\nTotal: at least {total} matches"

    if capped:
        result_text += f"\n\n... (capped at {max_results} results; use a narrower pattern for more)"

    return result_text


def _sum_rg_counts(lines: list[str]) -> int:
    """Sum the counts from ``rg -c`` output (``file:count`` format)."""
    total = 0
    for line in lines:
        idx = line.rfind(":")
        if idx > 0:
            try:
                total += int(line[idx + 1:])
            except ValueError:
                pass
    return total


# -----------------------------------------------------------------------
# Feature: Python fallback grep (with output modes & multiline)
# -----------------------------------------------------------------------

def _python_grep(
    pattern_str: str,
    search_path: Path,
    *,
    include: str | None,
    file_type: str | None,
    output_mode: str,
    multiline: bool,
    case_insensitive: bool,
    max_results: int,
    context_lines: int | None,
) -> str:
    """Pure-Python grep implementation used when rg is not available."""
    flags = 0
    if case_insensitive:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.DOTALL | re.MULTILINE

    try:
        regex = re.compile(pattern_str, flags)
    except re.error as exc:
        return f"Error: Invalid regex: {exc}"

    files = _iter_files(search_path, include, file_type)
    ctx = context_lines or 0

    # ------ files_with_matches mode ------
    if output_mode == "files_with_matches":
        matched_files: list[str] = []
        for fp in files:
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if regex.search(text):
                matched_files.append(_safe_relative(fp, search_path))
                if len(matched_files) >= max_results:
                    matched_files.append(f"... (capped at {max_results} files)")
                    break
        if not matched_files:
            return f"No matches for '{pattern_str}'"
        return "\n".join(matched_files)

    # ------ count mode ------
    if output_mode == "count":
        count_lines: list[str] = []
        total = 0
        for fp in files:
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            cnt = len(regex.findall(text))
            if cnt > 0:
                rel = _safe_relative(fp, search_path)
                count_lines.append(f"{rel}:{cnt}")
                total += cnt
                if len(count_lines) >= max_results:
                    count_lines.append(f"... (capped at {max_results} files)")
                    break
        if not count_lines:
            return f"No matches for '{pattern_str}'"
        count_lines.append(f"\nTotal: at least {total} matches")
        return "\n".join(count_lines)

    # ------ content mode (default) ------
    matches: list[str] = []

    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        file_lines = text.splitlines()
        for line_no, line in enumerate(file_lines, 1):
            if regex.search(line):
                rel = _safe_relative(file_path, search_path)

                if ctx > 0:
                    start = max(0, line_no - 1 - ctx)
                    end = min(len(file_lines), line_no + ctx)
                    for ci in range(start, end):
                        ctx_line = file_lines[ci]
                        display = ctx_line[:_MAX_LINE_LEN] + ("..." if len(ctx_line) > _MAX_LINE_LEN else "")
                        sep = ":" if ci == line_no - 1 else "-"
                        matches.append(f"{rel}{sep}{ci + 1}{sep} {display}")
                    matches.append("--")
                else:
                    display_line = line[:_MAX_LINE_LEN]
                    if len(line) > _MAX_LINE_LEN:
                        display_line += "..."
                    matches.append(f"{rel}:{line_no}: {display_line}")

                if len(matches) >= max_results:
                    matches.append(f"... (at least {max_results} matches, results capped)")
                    return "\n".join(matches)

    if not matches:
        return f"No matches for '{pattern_str}'"

    return "\n".join(matches)
