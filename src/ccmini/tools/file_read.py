"""FileReadTool — read file contents, optionally with line range."""

from __future__ import annotations

import base64
import itertools
import json
import os
from pathlib import Path
from typing import Any

from ..tool import Tool, ToolUseContext, resolve_path

_MAX_READ_SIZE = 512_000  # ~500KB

# Image extensions supported for base64 read
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

# Notebook extension
_NOTEBOOK_EXT = ".ipynb"


class FileReadTool(Tool):
    name = "Read"
    description = (
        "Read the contents of a file. Returns the file content with line numbers. "
        "Optionally specify offset (1-based line) and limit to read a range. "
        "Also supports images (base64), PDFs (text extraction), and Jupyter notebooks."
    )
    instructions = """\
Reads a file from the local filesystem. You can access any file directly \
by using this tool. Assume this tool can read all files on the machine. If \
the user provides a path to a file, assume that path is valid. It is OK to \
read a file that does not exist — a clear error will be returned.

Usage:
- The path parameter must be an absolute path, not a relative path.
- By default, reads up to 2000 lines from the beginning of the file.
- You can optionally specify offset (1-based line) and limit to read a \
specific range — especially useful for long files.
- Results are returned with line numbers (e.g. `  123|line content`). \
Treat the line-number prefix as metadata, not part of the actual code.
- You MUST use this tool at least once before editing a file. The Edit \
tool will error if you attempt an edit without reading the file first.
- This tool can only read files, not directories. To list a directory, \
use Bash with ls.

Image support:
- Supported formats: JPEG, PNG, GIF, WebP.
- Returns base64-encoded data and dimensions when available.

PDF support:
- PDF files are converted into text content automatically \
(requires pdfplumber or falls back to a basic message).

Notebook support:
- .ipynb files are parsed and cell contents returned.\
"""
    is_read_only = True

    def __init__(self, *, allowed_dirs: list[str] | None = None) -> None:
        self._allowed_dirs = [Path(d).resolve() for d in allowed_dirs] if allowed_dirs else None

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute file path"},
                "offset": {"type": "integer", "description": "Start line (1-based, default: 1)"},
                "limit": {"type": "integer", "description": "Number of lines to read (default: all)"},
            },
            "required": ["file_path"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        file_path = resolve_path(kwargs["file_path"], context)

        if self._allowed_dirs and not any(
            _is_subpath(file_path, d) for d in self._allowed_dirs
        ):
            return f"Error: Access denied — {file_path} is outside allowed directories."

        if not file_path.exists():
            return f"Error: File not found: {file_path}"
        if not file_path.is_file():
            return f"Error: Not a file: {file_path}"

        # Binary detection — bail early with a friendly message
        if _is_binary(file_path):
            file_type = detect_file_type(file_path)
            if file_type == "image":
                return _read_image(file_path)
            return f"Error: {file_path} appears to be a binary file (type: {file_type}). Cannot display as text."

        # Multi-format dispatch
        file_type = detect_file_type(file_path)
        if file_type == "image":
            return _read_image(file_path)
        if file_type == "pdf":
            result = _read_pdf(file_path)
            _notify_file_read(file_path, result)
            return result
        if file_type == "notebook":
            result = _read_notebook(file_path)
            _notify_file_read(file_path, result)
            return result

        # Standard text read
        offset = max(1, kwargs.get("offset", 1))
        limit = kwargs.get("limit")

        # Use efficient line-range reading for narrow ranges on large files
        if limit is not None and limit < 500:
            result = _read_lines_efficient(file_path, offset, offset + limit - 1)
            if result is not None:
                _update_read_state(file_path)
                return result

        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return f"Error reading file: {exc}"

        if len(text) > _MAX_READ_SIZE:
            text = text[:_MAX_READ_SIZE]
            truncated = True
        else:
            truncated = False

        lines = text.splitlines(keepends=True)

        if limit is not None:
            selected = lines[offset - 1:offset - 1 + limit]
        else:
            selected = lines[offset - 1:]

        numbered = []
        for i, line in enumerate(selected, start=offset):
            numbered.append(f"{i:6d}|{line.rstrip()}")

        result = "\n".join(numbered)
        if truncated:
            result += "\n... (file truncated, showing first 500KB)"
        if not result:
            return "(empty file)"

        # Track read for concurrent-edit protection
        _update_read_state(file_path)
        _notify_file_read(file_path, text)

        return result


def _is_subpath(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


# -----------------------------------------------------------------------
# Feature: ReadFileState tracking
# -----------------------------------------------------------------------

def _update_read_state(file_path: Path) -> None:
    """Record the file's mtime after a read, used by FileEditTool."""
    try:
        from .file_edit import _read_file_state
        _read_file_state[str(file_path)] = os.path.getmtime(file_path)
    except Exception:
        pass


def _notify_file_read(file_path: Path, content: str) -> None:
    """Notify optional file-read listeners, such as Magic Docs."""
    try:
        from ..services.magic_docs import notify_file_read

        if content and not content.startswith("Error:"):
            notify_file_read(str(file_path), content)
    except Exception:
        pass


def get_read_file_state() -> dict[str, float]:
    """Proxy to the shared state in file_edit."""
    try:
        from .file_edit import _read_file_state
        return _read_file_state
    except Exception:
        return {}


# -----------------------------------------------------------------------
# Feature: Multi-format support
# -----------------------------------------------------------------------

_PDF_EXTENSIONS = frozenset({".pdf"})
_EXT_TO_TYPE: dict[str, str] = {}
for _ext in _IMAGE_EXTENSIONS:
    _EXT_TO_TYPE[_ext] = "image"
for _ext in _PDF_EXTENSIONS:
    _EXT_TO_TYPE[_ext] = "pdf"
_EXT_TO_TYPE[_NOTEBOOK_EXT] = "notebook"


def detect_file_type(path: Path) -> str:
    """Return a file-type string based on extension: 'image', 'pdf', 'notebook', or 'text'."""
    suffix = path.suffix.lower()
    return _EXT_TO_TYPE.get(suffix, "text")


def _read_image(path: Path) -> str:
    """Return base64-encoded image data and metadata."""
    try:
        data = path.read_bytes()
    except Exception as exc:
        return f"Error reading image: {exc}"

    encoded = base64.b64encode(data).decode("ascii")
    size_kb = len(data) / 1024
    suffix = path.suffix.lower().lstrip(".")
    mime = f"image/{suffix}" if suffix != "jpg" else "image/jpeg"

    dimensions = _get_image_dimensions(data)
    dim_str = f", dimensions: {dimensions[0]}x{dimensions[1]}" if dimensions else ""

    return (
        f"Image: {path.name} ({size_kb:.1f} KB, {mime}{dim_str})\n"
        f"data:{mime};base64,{encoded[:200]}... ({len(encoded)} chars total)"
    )


def _get_image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Try to extract width/height from image bytes without external deps."""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data))
        return img.size
    except Exception:
        pass

    # PNG header: width at bytes 16-20, height at 20-24
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 24:
        import struct
        w, h = struct.unpack(">II", data[16:24])
        return (w, h)

    return None


def _read_pdf(path: Path) -> str:
    """Extract text from a PDF file."""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            pages_text: list[str] = []
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                pages_text.append(f"--- Page {i} ---\n{text}")
            return "\n\n".join(pages_text) if pages_text else "(empty PDF)"
    except ImportError:
        pass

    try:
        import PyPDF2
        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            pages_text = []
            for i, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                pages_text.append(f"--- Page {i} ---\n{text}")
            return "\n\n".join(pages_text) if pages_text else "(empty PDF)"
    except ImportError:
        pass

    return (
        f"PDF file: {path.name} ({path.stat().st_size / 1024:.1f} KB). "
        "Install pdfplumber or PyPDF2 to extract text content."
    )


def _read_notebook(path: Path) -> str:
    """Parse a Jupyter .ipynb file and return cell contents."""
    try:
        raw = path.read_text(encoding="utf-8")
        nb = json.loads(raw)
    except Exception as exc:
        return f"Error reading notebook: {exc}"

    cells = nb.get("cells", [])
    if not cells:
        return "(empty notebook)"

    parts: list[str] = []
    for i, cell in enumerate(cells, 1):
        cell_type = cell.get("cell_type", "unknown")
        source = "".join(cell.get("source", []))
        parts.append(f"--- Cell {i} [{cell_type}] ---\n{source}")

    return "\n\n".join(parts)


# -----------------------------------------------------------------------
# Feature: Line range optimization
# -----------------------------------------------------------------------

def _read_lines_efficient(
    path: Path, start: int, end: int,
) -> str | None:
    """Read only lines *start* through *end* (1-based, inclusive) efficiently.

    Returns None if an error occurs so the caller can fall back to full read.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            selected = list(itertools.islice(fh, start - 1, end))
    except Exception:
        return None

    if not selected:
        return "(empty range)"

    numbered = []
    for i, line in enumerate(selected, start=start):
        numbered.append(f"{i:6d}|{line.rstrip()}")
    return "\n".join(numbered)


# -----------------------------------------------------------------------
# Feature: Binary detection
# -----------------------------------------------------------------------

_BINARY_CHECK_SIZE = 8192


def _is_binary(path: Path) -> bool:
    """Return True if *path* appears to be a binary file.

    Checks for null bytes in the first 8 KB of the file. Files with
    known non-text extensions (images, PDFs) still pass through so
    their dedicated readers can handle them.
    """
    suffix = path.suffix.lower()
    if suffix in _IMAGE_EXTENSIONS or suffix in _PDF_EXTENSIONS:
        return True
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(_BINARY_CHECK_SIZE)
        return b"\x00" in chunk
    except Exception:
        return False
