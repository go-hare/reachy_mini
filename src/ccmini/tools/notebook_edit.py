"""NotebookEditTool — edit Jupyter notebook (.ipynb) cells.

Provides cell-level operations on Jupyter notebooks: edit, insert, delete,
move, read, and list.  Preserves notebook metadata, outputs, and execution
counts where appropriate.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from ..tool import Tool, ToolUseContext, resolve_path

# Maximum notebook file size to process
_MAX_NOTEBOOK_SIZE = 50_000_000  # 50 MB


class NotebookEditTool(Tool):
    name = "NotebookEdit"
    description = (
        "Edit Jupyter notebook cells — replace, insert, delete, move, or "
        "read individual cells in .ipynb files."
    )
    instructions = """\
Edit Jupyter notebook (.ipynb) cells.

Supported operations via the 'action' parameter:
- edit_cell: Replace the source of an existing cell
- insert_cell: Insert a new cell at a given index
- delete_cell: Delete a cell by index
- move_cell: Move a cell from one index to another
- get_cell: Read a single cell's contents
- list_cells: List all cells with their indices, types, and previews

Parameters:
- notebook_path: Path to the .ipynb file (required for all operations)
- action: The operation to perform (required)
- cell_index: 0-based cell index (required for edit/delete/move/get)
- new_source: New cell source content (required for edit_cell and insert_cell)
- cell_type: Cell type — "code", "markdown", or "raw" (for insert_cell; \
default: "code")
- to_index: Target index (required for move_cell)

Notes:
- Cell indices are 0-based.
- Editing a code cell clears its outputs and resets execution_count.
- Inserting a cell shifts subsequent cell indices.
- The notebook structure is validated before and after operations.
- Metadata, kernel info, and nbformat versions are preserved.\
"""
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "notebook_path": {
                    "type": "string",
                    "description": "Path to the .ipynb notebook file",
                },
                "action": {
                    "type": "string",
                    "description": "Operation to perform",
                    "enum": [
                        "edit_cell",
                        "insert_cell",
                        "delete_cell",
                        "move_cell",
                        "get_cell",
                        "list_cells",
                    ],
                },
                "cell_index": {
                    "type": "integer",
                    "description": "0-based cell index",
                },
                "new_source": {
                    "type": "string",
                    "description": "New source content for the cell",
                },
                "cell_type": {
                    "type": "string",
                    "description": "Cell type for insert (code, markdown, raw)",
                    "enum": ["code", "markdown", "raw"],
                },
                "to_index": {
                    "type": "integer",
                    "description": "Target index for move_cell",
                },
            },
            "required": ["notebook_path", "action"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        notebook_path: str = kwargs["notebook_path"]
        action: str = kwargs["action"]
        cell_index: int | None = kwargs.get("cell_index")
        new_source: str | None = kwargs.get("new_source")
        cell_type: str = kwargs.get("cell_type", "code")
        to_index: int | None = kwargs.get("to_index")

        path = resolve_path(notebook_path, context)

        if action in ("edit_cell", "delete_cell", "move_cell", "get_cell"):
            if cell_index is None:
                return f"Error: 'cell_index' is required for {action}"
        if action in ("edit_cell", "insert_cell"):
            if new_source is None:
                return f"Error: 'new_source' is required for {action}"
        if action == "move_cell" and to_index is None:
            return "Error: 'to_index' is required for move_cell"

        # Read-only actions don't need file to exist for validation first
        if action == "list_cells" or action == "get_cell":
            return self._read_action(path, action, cell_index)

        # Write actions
        if action == "insert_cell":
            return self._insert_cell(path, cell_index, new_source or "", cell_type)
        if action == "edit_cell":
            assert cell_index is not None
            return self._edit_cell(path, cell_index, new_source or "", cell_type)
        if action == "delete_cell":
            assert cell_index is not None
            return self._delete_cell(path, cell_index)
        if action == "move_cell":
            assert cell_index is not None and to_index is not None
            return self._move_cell(path, cell_index, to_index)

        return f"Error: Unknown action '{action}'"

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def _read_action(self, path: Path, action: str, cell_index: int | None) -> str:
        nb = _load_notebook(path)
        if isinstance(nb, str):
            return nb  # error string

        cells = nb.get("cells", [])

        if action == "list_cells":
            return _format_cell_list(cells, path)

        if action == "get_cell":
            assert cell_index is not None
            if cell_index < 0 or cell_index >= len(cells):
                return f"Error: cell_index {cell_index} out of range (0–{len(cells) - 1})"
            cell = cells[cell_index]
            return _format_single_cell(cell, cell_index)

        return "Error: Unknown read action"

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def _edit_cell(self, path: Path, cell_index: int, new_source: str, cell_type: str | None) -> str:
        nb = _load_notebook(path)
        if isinstance(nb, str):
            return nb

        cells = nb.get("cells", [])
        if cell_index < 0 or cell_index >= len(cells):
            return f"Error: cell_index {cell_index} out of range (0–{len(cells) - 1})"

        cell = cells[cell_index]
        cell["source"] = _to_source_format(new_source)

        if cell_type and cell_type != cell.get("cell_type"):
            cell["cell_type"] = cell_type

        if cell.get("cell_type") == "code":
            cell["execution_count"] = None
            cell["outputs"] = []

        err = _save_notebook(path, nb)
        if err:
            return err

        return f"Successfully edited cell {cell_index} in {path.name}"

    def _insert_cell(self, path: Path, cell_index: int | None, source: str, cell_type: str) -> str:
        nb = _load_notebook(path)
        if isinstance(nb, str):
            return nb

        cells = nb.get("cells", [])
        insert_at = cell_index if cell_index is not None else len(cells)
        insert_at = max(0, min(insert_at, len(cells)))

        new_cell = _make_cell(cell_type, source, nb)
        cells.insert(insert_at, new_cell)

        err = _save_notebook(path, nb)
        if err:
            return err

        return f"Inserted {cell_type} cell at index {insert_at} in {path.name}"

    def _delete_cell(self, path: Path, cell_index: int) -> str:
        nb = _load_notebook(path)
        if isinstance(nb, str):
            return nb

        cells = nb.get("cells", [])
        if cell_index < 0 or cell_index >= len(cells):
            return f"Error: cell_index {cell_index} out of range (0–{len(cells) - 1})"

        if len(cells) == 1:
            return "Error: Cannot delete the last remaining cell."

        removed = cells.pop(cell_index)
        removed_type = removed.get("cell_type", "unknown")

        err = _save_notebook(path, nb)
        if err:
            return err

        return f"Deleted {removed_type} cell at index {cell_index} from {path.name}"

    def _move_cell(self, path: Path, from_index: int, to_index: int) -> str:
        nb = _load_notebook(path)
        if isinstance(nb, str):
            return nb

        cells = nb.get("cells", [])
        n = len(cells)
        if from_index < 0 or from_index >= n:
            return f"Error: from_index {from_index} out of range (0–{n - 1})"
        if to_index < 0 or to_index >= n:
            return f"Error: to_index {to_index} out of range (0–{n - 1})"
        if from_index == to_index:
            return "No move needed — indices are the same."

        cell = cells.pop(from_index)
        cells.insert(to_index, cell)

        err = _save_notebook(path, nb)
        if err:
            return err

        return f"Moved cell from index {from_index} to {to_index} in {path.name}"


# ---------------------------------------------------------------------------
# Notebook I/O helpers
# ---------------------------------------------------------------------------

def _load_notebook(path: Path) -> dict[str, Any] | str:
    """Load and validate a notebook, returning the parsed dict or an error string."""
    if not path.exists():
        return f"Error: Notebook not found: {path}"
    if not path.is_file():
        return f"Error: Not a file: {path}"
    if path.suffix.lower() != ".ipynb":
        return "Error: File must be a Jupyter notebook (.ipynb)"

    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"Error: Cannot stat file: {exc}"
    if size > _MAX_NOTEBOOK_SIZE:
        return f"Error: Notebook too large ({size / 1_000_000:.1f} MB, max {_MAX_NOTEBOOK_SIZE / 1_000_000:.0f} MB)"

    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Error reading notebook: {exc}"

    try:
        nb = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"Error: Notebook is not valid JSON: {exc}"

    if err := _validate_notebook_structure(nb):
        return err

    return nb


def _save_notebook(path: Path, nb: dict[str, Any]) -> str | None:
    """Write notebook back to disk.  Returns an error string or None on success."""
    if err := _validate_notebook_structure(nb):
        return f"Error: Post-edit validation failed: {err}"

    try:
        content = json.dumps(nb, indent=1, ensure_ascii=False)
        path.write_text(content, encoding="utf-8")
    except Exception as exc:
        return f"Error writing notebook: {exc}"

    return None


def _validate_notebook_structure(nb: dict[str, Any]) -> str | None:
    """Basic structural validation.  Returns error string or None."""
    if not isinstance(nb, dict):
        return "Error: Notebook root must be a JSON object"
    if "cells" not in nb:
        return "Error: Notebook missing 'cells' array"
    if not isinstance(nb["cells"], list):
        return "Error: 'cells' must be an array"
    if "nbformat" not in nb:
        return "Error: Notebook missing 'nbformat'"
    for i, cell in enumerate(nb["cells"]):
        if not isinstance(cell, dict):
            return f"Error: Cell {i} is not a JSON object"
        if "cell_type" not in cell:
            return f"Error: Cell {i} missing 'cell_type'"
        if "source" not in cell:
            return f"Error: Cell {i} missing 'source'"
    return None


# ---------------------------------------------------------------------------
# Cell helpers
# ---------------------------------------------------------------------------

def _make_cell(cell_type: str, source: str, nb: dict[str, Any]) -> dict[str, Any]:
    """Create a new notebook cell dict with appropriate fields."""
    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "source": _to_source_format(source),
        "metadata": {},
    }

    # Assign cell ID for nbformat >= 4.5
    nbformat = nb.get("nbformat", 4)
    nbformat_minor = nb.get("nbformat_minor", 0)
    if nbformat > 4 or (nbformat == 4 and nbformat_minor >= 5):
        cell["id"] = uuid.uuid4().hex[:12]

    if cell_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []

    return cell


def _to_source_format(source: str) -> str | list[str]:
    """Convert source to the format used in the notebook.

    Notebooks can store source as a single string or as a list of strings
    (one per line).  We use a single string for simplicity — most modern
    notebook implementations accept both.
    """
    return source


def _detect_language(nb: dict[str, Any]) -> str:
    """Detect the notebook's primary language from kernel metadata."""
    meta = nb.get("metadata", {})
    lang_info = meta.get("language_info", {})
    lang = lang_info.get("name", "")
    if lang:
        return lang
    kernelspec = meta.get("kernelspec", {})
    kname = kernelspec.get("language", "") or kernelspec.get("name", "")
    if "python" in kname.lower():
        return "python"
    if "julia" in kname.lower():
        return "julia"
    if "r" in kname.lower():
        return "r"
    return kname or "unknown"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_MAX_PREVIEW_CHARS = 120


def _format_cell_list(cells: list[dict[str, Any]], path: Path) -> str:
    """Format a summary listing of all cells."""
    if not cells:
        return f"Notebook {path.name} is empty (no cells)."

    lines: list[str] = [f"Notebook: {path.name} ({len(cells)} cells)"]
    for i, cell in enumerate(cells):
        ct = cell.get("cell_type", "unknown")
        source = _get_source_str(cell)
        preview = source.replace("\n", " ").strip()
        if len(preview) > _MAX_PREVIEW_CHARS:
            preview = preview[:_MAX_PREVIEW_CHARS] + "..."
        cell_id = cell.get("id", "")
        id_str = f" id={cell_id}" if cell_id else ""
        lines.append(f"  [{i}] {ct}{id_str}: {preview}")
    return "\n".join(lines)


def _format_single_cell(cell: dict[str, Any], index: int) -> str:
    """Format a single cell for display."""
    ct = cell.get("cell_type", "unknown")
    source = _get_source_str(cell)
    cell_id = cell.get("id", "")
    id_str = f", id={cell_id}" if cell_id else ""
    exec_count = cell.get("execution_count")
    exec_str = f", execution_count={exec_count}" if exec_count is not None else ""

    header = f"Cell {index} ({ct}{id_str}{exec_str}):"

    outputs = cell.get("outputs", [])
    output_summary = ""
    if outputs:
        output_lines: list[str] = []
        for j, out in enumerate(outputs):
            otype = out.get("output_type", "unknown")
            if otype == "stream":
                text = "".join(out.get("text", []))
                output_lines.append(f"  Output {j} (stream): {text[:200]}")
            elif otype in ("display_data", "execute_result"):
                data = out.get("data", {})
                keys = list(data.keys())
                output_lines.append(f"  Output {j} ({otype}): {keys}")
            elif otype == "error":
                ename = out.get("ename", "")
                evalue = out.get("evalue", "")
                output_lines.append(f"  Output {j} (error): {ename}: {evalue}")
            else:
                output_lines.append(f"  Output {j} ({otype})")
        output_summary = "\nOutputs:\n" + "\n".join(output_lines)

    return f"{header}\n{source}{output_summary}"


def _get_source_str(cell: dict[str, Any]) -> str:
    """Extract source as a plain string regardless of storage format."""
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(source)
    return str(source)
