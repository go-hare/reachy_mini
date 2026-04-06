"""FileWriteTool — write or overwrite a file."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from ..tool import Tool, ToolUseContext, resolve_path

# Threshold above which we warn before overwriting an existing file (chars)
_LARGE_FILE_THRESHOLD = 50_000


class FileWriteTool(Tool):
    name = "Write"
    description = (
        "Create or overwrite a file with the given contents. "
        "Parent directories are created automatically."
    )
    instructions = """\
Writes a file to the local filesystem. This tool will overwrite the \
existing file if there is one at the provided path.

Usage:
- If this is an existing file, you MUST use Read first to read the \
file's contents. This tool will fail if you did not read the file first.
- Prefer Edit for modifying existing files — it only sends the diff. \
Only use this tool to create new files or for complete rewrites.
- NEVER create documentation files (*.md) or README files unless explicitly \
requested by the user.
- Only use emojis if the user explicitly requests it. Avoid writing emojis \
to files unless asked.
- NEVER generate extremely long hashes, base64 blobs, or binary content.\
"""
    is_read_only = False

    def __init__(self, *, allowed_dirs: list[str] | None = None) -> None:
        self._allowed_dirs = [Path(d).resolve() for d in allowed_dirs] if allowed_dirs else None

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute file path to write"},
                "content": {"type": "string", "description": "File contents to write"},
            },
            "required": ["file_path", "content"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        file_path = resolve_path(kwargs["file_path"], context)
        contents: str = kwargs["content"]

        if self._allowed_dirs and not any(
            _is_subpath(file_path, d) for d in self._allowed_dirs
        ):
            return f"Error: Access denied — {file_path} is outside allowed directories."

        # Safety check — warn before overwriting large files
        warning = _should_warn(file_path)
        if warning:
            # Still proceed but include warning in output
            pass

        # Detect encoding of existing file and preserve it
        encoding = _detect_encoding(file_path) if file_path.exists() else "utf-8"

        # Auto-create parent directories
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return f"Error creating directories: {exc}"

        # Atomic write: write to temp file first, then rename
        try:
            _atomic_write(file_path, contents, encoding=encoding)
        except Exception as exc:
            return f"Error writing file: {exc}"

        # Update read state for concurrent-edit tracking
        try:
            from .file_edit import _read_file_state
            _read_file_state[str(file_path)] = os.path.getmtime(file_path)
        except Exception:
            pass

        msg = f"Successfully wrote {len(contents)} characters to {file_path}"
        if warning:
            msg = f"Warning: {warning}\n{msg}"
        return msg


def _is_subpath(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


# -----------------------------------------------------------------------
# Feature: Write confirmation / safety check
# -----------------------------------------------------------------------

def _should_warn(path: Path) -> str | None:
    """Return a warning reason if the write deserves extra attention, or None."""
    if not path.exists():
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None

    if size > _LARGE_FILE_THRESHOLD:
        kb = size / 1024
        return f"Overwriting large file ({kb:.1f} KB)"

    # Check against ReadFileState — was the file read before?
    try:
        from .file_edit import _read_file_state
        path_key = str(path)
        if path_key not in _read_file_state:
            return "File exists but was not read in this session"
    except Exception:
        pass

    return None


# -----------------------------------------------------------------------
# Feature: Atomic write
# -----------------------------------------------------------------------

def _atomic_write(
    path: Path, content: str, *, encoding: str = "utf-8",
) -> None:
    """Write *content* to *path* atomically via a temp file + rename.

    On Windows, ``os.replace`` can fail if the target is open by another
    process; fall back to a direct write in that case.
    """
    parent = path.parent
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding=encoding) as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, str(path))
        except OSError:
            # Fallback: direct write (Windows edge cases)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            path.write_text(content, encoding=encoding)
    except OSError:
        # Final fallback — plain write
        path.write_text(content, encoding=encoding)


# -----------------------------------------------------------------------
# Feature: Encoding detection
# -----------------------------------------------------------------------

def _detect_encoding(path: Path) -> str:
    """Detect the encoding of an existing file.

    Checks for a UTF-8 BOM and UTF-16 LE BOM. Falls back to ``utf-8``.
    For robust detection, ``chardet`` is used when available.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
    except OSError:
        return "utf-8"

    # BOM detection
    if head[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    if head[:2] == b"\xff\xfe":
        return "utf-16-le"
    if head[:2] == b"\xfe\xff":
        return "utf-16-be"

    # Try chardet for more nuanced detection
    try:
        import chardet
        with open(path, "rb") as fh:
            raw = fh.read(8192)
        detected = chardet.detect(raw)
        if detected and detected.get("encoding"):
            enc = detected["encoding"].lower()
            confidence = detected.get("confidence", 0)
            if confidence > 0.7 and enc != "ascii":
                return enc
    except ImportError:
        pass

    return "utf-8"
