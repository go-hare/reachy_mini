"""FileEditTool — exact string replacement in files."""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..tool import Tool, ToolUseContext, resolve_path

# Shared mutable state tracking file read times — keyed by resolved path.
# FileReadTool populates this; FileEditTool checks it before editing.
_read_file_state: dict[str, float] = {}

# Undo history — keyed by resolved path string → original content before edit.
_undo_state: dict[str, str] = {}


class FileEditTool(Tool):
    name = "Edit"
    description = (
        "Replace an exact string in a file. The old_string must appear exactly "
        "once in the file (unless replace_all is true). Use for surgical edits."
    )
    instructions = """\
Performs exact string replacements in files.

Usage:
- You must use Read at least once in the conversation before editing. \
This tool will error if you attempt an edit without reading the file first.
- When editing text from file_read output, ensure you preserve the exact \
indentation (tabs/spaces) as it appears AFTER the line number prefix. The \
line number prefix is metadata — never include it in old_string or new_string.
- ALWAYS prefer editing existing files in the codebase. NEVER write new \
files unless explicitly required.
- Only use emojis if the user explicitly requests it. Avoid adding emojis \
to files unless asked.
- The edit will FAIL if old_string is not unique in the file. Either provide \
a larger string with more surrounding context to make it unique, or use \
replace_all to change every instance.
- Use replace_all for replacing and renaming strings across the file — \
useful for renaming a variable, for instance.\
"""
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute file path to edit"},
                "old_string": {"type": "string", "description": "Exact text to find"},
                "new_string": {"type": "string", "description": "Replacement text"},
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default: false)",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        file_path = resolve_path(kwargs["file_path"], context)
        old_string: str = kwargs["old_string"]
        new_string: str = kwargs["new_string"]
        replace_all: bool = kwargs.get("replace_all", False)

        if not file_path.exists():
            return f"Error: File not found: {file_path}"
        if not file_path.is_file():
            return f"Error: Not a file: {file_path}"

        # Concurrent edit protection: check if file was modified since last read
        path_key = str(file_path)
        if warn := _check_concurrent_modification(path_key):
            return warn

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            return f"Error reading file: {exc}"

        count = content.count(old_string)
        if count == 0:
            return "Error: old_string not found in file."
        if count > 1 and not replace_all:
            return (
                f"Error: old_string found {count} times. "
                f"Provide more context to make it unique, or set replace_all=true."
            )

        # Save undo state before modifying
        _save_undo_state(path_key, content)

        if replace_all:
            new_content = content.replace(old_string, new_string)
            replaced = count
        else:
            new_content = content.replace(old_string, new_string, 1)
            replaced = 1

        try:
            file_path.write_text(new_content, encoding="utf-8")
        except Exception as exc:
            return f"Error writing file: {exc}"

        # Update read state so subsequent edits see the new mtime
        _read_file_state[path_key] = os.path.getmtime(file_path)

        # Generate diff for visibility
        diff_text = _generate_diff(content, new_content, str(file_path))

        # Run lint check if available
        lint_output = _run_lint_check(file_path)

        parts = [f"Successfully replaced {replaced} occurrence(s) in {file_path}"]
        if diff_text:
            parts.append(f"\nDiff:\n{diff_text}")
        if lint_output:
            parts.append(f"\nLint issues:\n{lint_output}")
        return "\n".join(parts)


# -----------------------------------------------------------------------
# Feature: Concurrent edit protection (ReadFileState tracking)
# -----------------------------------------------------------------------

def get_read_file_state() -> dict[str, float]:
    """Return the shared read-file-state dict (path → mtime)."""
    return _read_file_state


def _check_concurrent_modification(path_key: str) -> str | None:
    """Return an error string if the file was modified since last read."""
    read_mtime = _read_file_state.get(path_key)
    if read_mtime is None:
        return None
    try:
        current_mtime = os.path.getmtime(path_key)
    except OSError:
        return None
    if current_mtime > read_mtime:
        return (
            "Error: File has been modified since it was last read "
            "(either by the user or by another tool). "
            "Read it again before editing."
        )
    return None


# -----------------------------------------------------------------------
# Feature: Lint integration
# -----------------------------------------------------------------------

_LINTER_MAP: dict[str, list[str]] = {
    ".py": ["ruff", "check", "--quiet"],
    ".js": ["eslint", "--no-color"],
    ".ts": ["eslint", "--no-color"],
    ".tsx": ["eslint", "--no-color"],
    ".jsx": ["eslint", "--no-color"],
    ".rs": ["cargo", "clippy", "--message-format=short"],
    ".go": ["golangci-lint", "run"],
}


def detect_linter(file_path: Path) -> list[str] | None:
    """Return the linter command for *file_path* based on extension, or None."""
    suffix = file_path.suffix.lower()
    cmd = _LINTER_MAP.get(suffix)
    if cmd is None:
        return None
    if shutil.which(cmd[0]) is None:
        return None
    return cmd


def _run_lint_check(file_path: Path) -> str | None:
    """Run the appropriate linter on *file_path* and return any output."""
    cmd = detect_linter(file_path)
    if cmd is None:
        return None
    try:
        full_cmd = cmd + [str(file_path)]
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return output.strip() or None
    except Exception:
        return None


# -----------------------------------------------------------------------
# Feature: Undo support
# -----------------------------------------------------------------------

def _save_undo_state(path_key: str, original_content: str) -> None:
    """Save the content before an edit for potential undo."""
    _undo_state[path_key] = original_content


def get_undo_state() -> dict[str, str]:
    """Return the undo state dict (path → original content before last edit)."""
    return _undo_state


def undo_last_edit(path: str) -> str:
    """Restore a file to its pre-edit state. Returns a status message."""
    resolved = str(Path(path).resolve())
    original = _undo_state.pop(resolved, None)
    if original is None:
        return f"Error: No undo state available for {resolved}"
    try:
        Path(resolved).write_text(original, encoding="utf-8")
        _read_file_state[resolved] = os.path.getmtime(resolved)
        return f"Successfully restored {resolved} to its pre-edit state."
    except Exception as exc:
        return f"Error restoring file: {exc}"


# -----------------------------------------------------------------------
# Feature: Diff output
# -----------------------------------------------------------------------

def _generate_diff(
    original: str, modified: str, path: str, context_lines: int = 3,
) -> str:
    """Generate a unified diff between *original* and *modified*."""
    orig_lines = original.splitlines(keepends=True)
    mod_lines = modified.splitlines(keepends=True)
    diff = difflib.unified_diff(
        orig_lines, mod_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=context_lines,
    )
    return "".join(diff)
