"""LSPTool — Language Server Protocol integration for code intelligence.

Provides go-to-definition, find-references, hover info, document/workspace
symbols, diagnostics, rename, and code-action operations by driving LSP
servers as subprocesses.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..tool import Tool, ToolUseContext

# ---------------------------------------------------------------------------
# LSP operations supported by this tool
# ---------------------------------------------------------------------------

_OPERATIONS = (
    "goto_definition",
    "find_references",
    "hover",
    "document_symbols",
    "workspace_symbols",
    "diagnostics",
    "rename",
    "code_actions",
)

# Position-based operations requiring file + line + column
_POSITION_OPS = {
    "goto_definition",
    "find_references",
    "hover",
    "rename",
    "code_actions",
}

# File-only operations
_FILE_OPS = {"document_symbols", "diagnostics"}

# Query-based operations
_QUERY_OPS = {"workspace_symbols"}

# ---------------------------------------------------------------------------
# Known LSP servers by language
# ---------------------------------------------------------------------------

_LANGUAGE_SERVERS: dict[str, list[str]] = {
    "python": ["pyright-langserver", "--stdio"],
    "python-alt": ["pylsp"],
    "javascript": ["typescript-language-server", "--stdio"],
    "typescript": ["typescript-language-server", "--stdio"],
    "go": ["gopls", "serve"],
    "rust": ["rust-analyzer"],
    "c": ["clangd"],
    "cpp": ["clangd"],
}

_EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
}

# LSP message-ID counter
_next_id = 0


def _new_id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


# ---------------------------------------------------------------------------
# LSP JSON-RPC helpers
# ---------------------------------------------------------------------------

def _encode_message(obj: dict[str, Any]) -> bytes:
    """Encode a JSON-RPC message with Content-Length header."""
    body = json.dumps(obj).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n"
    return header.encode("ascii") + body


async def _read_message(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """Read a single JSON-RPC message from the LSP server."""
    content_length = -1
    while True:
        line = await asyncio.wait_for(reader.readline(), timeout=30.0)
        if not line:
            return None
        line_str = line.decode("ascii", errors="replace").strip()
        if not line_str:
            break
        if line_str.lower().startswith("content-length:"):
            content_length = int(line_str.split(":", 1)[1].strip())

    if content_length < 0:
        return None

    body = await asyncio.wait_for(reader.readexactly(content_length), timeout=30.0)
    return json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# LSP Server Manager
# ---------------------------------------------------------------------------

@dataclass
class _LSPConnection:
    """A running LSP server process."""
    language: str
    process: asyncio.subprocess.Process
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter | Any  # stdin pipe
    initialized: bool = False
    pending: dict[int, asyncio.Future[Any]] = field(default_factory=dict)
    reader_task: asyncio.Task[None] | None = None


class LSPServerManager:
    """Manages LSP server processes per language."""

    def __init__(self) -> None:
        self._connections: dict[str, _LSPConnection] = {}

    async def get_connection(self, language: str, root_path: str | None = None) -> _LSPConnection | None:
        if language in self._connections:
            conn = self._connections[language]
            if conn.process.returncode is None:
                return conn
            del self._connections[language]

        return await self._start_server(language, root_path)

    async def _start_server(self, language: str, root_path: str | None = None) -> _LSPConnection | None:
        cmd_parts = _LANGUAGE_SERVERS.get(language)
        if cmd_parts is None:
            return None

        executable = cmd_parts[0]
        if shutil.which(executable) is None:
            # Try alternative server name
            alt_key = f"{language}-alt"
            alt_parts = _LANGUAGE_SERVERS.get(alt_key)
            if alt_parts and shutil.which(alt_parts[0]):
                cmd_parts = alt_parts
            else:
                return None

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=root_path,
            )
        except Exception:
            return None

        if proc.stdout is None or proc.stdin is None:
            return None

        conn = _LSPConnection(
            language=language,
            process=proc,
            reader=proc.stdout,
            writer=proc.stdin,
        )

        conn.reader_task = asyncio.create_task(self._read_loop(conn))
        self._connections[language] = conn

        await self._initialize(conn, root_path or os.getcwd())
        return conn

    async def _initialize(self, conn: _LSPConnection, root_path: str) -> None:
        root_uri = Path(root_path).as_uri()
        response = await self._send_request(conn, "initialize", {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                    "hover": {"contentFormat": ["plaintext", "markdown"]},
                    "documentSymbol": {"dynamicRegistration": False},
                    "publishDiagnostics": {"relatedInformation": True},
                    "rename": {"dynamicRegistration": False},
                    "codeAction": {"dynamicRegistration": False},
                },
                "workspace": {
                    "symbol": {"dynamicRegistration": False},
                },
            },
        })
        if response is not None:
            self._send_notification(conn, "initialized", {})
            conn.initialized = True

    async def _send_request(
        self, conn: _LSPConnection, method: str, params: dict[str, Any],
    ) -> Any:
        msg_id = _new_id()
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
        loop = asyncio.get_event_loop()
        future: asyncio.Future[Any] = loop.create_future()
        conn.pending[msg_id] = future
        conn.writer.write(_encode_message(msg))
        await conn.writer.drain()
        try:
            return await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            conn.pending.pop(msg_id, None)
            return None

    def _send_notification(
        self, conn: _LSPConnection, method: str, params: dict[str, Any],
    ) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        conn.writer.write(_encode_message(msg))

    async def _read_loop(self, conn: _LSPConnection) -> None:
        try:
            while conn.process.returncode is None:
                msg = await _read_message(conn.reader)
                if msg is None:
                    break
                msg_id = msg.get("id")
                if msg_id is not None and msg_id in conn.pending:
                    future = conn.pending.pop(msg_id)
                    if "error" in msg:
                        future.set_result(msg["error"])
                    else:
                        future.set_result(msg.get("result"))
        except Exception:
            pass
        finally:
            for future in conn.pending.values():
                if not future.done():
                    future.set_result(None)
            conn.pending.clear()

    async def send_request(
        self, language: str, method: str, params: dict[str, Any],
        root_path: str | None = None,
    ) -> Any:
        conn = await self.get_connection(language, root_path)
        if conn is None or not conn.initialized:
            return None
        return await self._send_request(conn, method, params)

    async def open_file(self, file_path: str, language: str, root_path: str | None = None) -> None:
        conn = await self.get_connection(language, root_path)
        if conn is None or not conn.initialized:
            return
        uri = Path(file_path).as_uri()
        try:
            content = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return
        self._send_notification(conn, "textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": language,
                "version": 1,
                "text": content,
            },
        })

    async def shutdown_all(self) -> None:
        for conn in self._connections.values():
            try:
                await self._send_request(conn, "shutdown", {})
                self._send_notification(conn, "exit", {})
                conn.process.kill()
            except Exception:
                pass
            if conn.reader_task:
                conn.reader_task.cancel()
        self._connections.clear()


# Module-level singleton
_manager: LSPServerManager | None = None


def _get_manager() -> LSPServerManager:
    global _manager
    if _manager is None:
        _manager = LSPServerManager()
    return _manager


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_location(loc: dict[str, Any], cwd: str | None = None) -> str:
    """Format a single LSP Location into a human-readable string."""
    uri = loc.get("uri", "")
    file_path = _uri_to_path(uri)
    if cwd:
        try:
            file_path = str(Path(file_path).relative_to(cwd))
        except ValueError:
            pass
    rng = loc.get("range", {})
    start = rng.get("start", {})
    line = start.get("line", 0) + 1
    col = start.get("character", 0) + 1
    return f"{file_path}:{line}:{col}"


def format_symbol(sym: dict[str, Any], cwd: str | None = None) -> str:
    """Format a SymbolInformation or DocumentSymbol."""
    name = sym.get("name", "<unknown>")
    kind = _SYMBOL_KIND_NAMES.get(sym.get("kind", 0), "unknown")
    loc = sym.get("location")
    if loc:
        where = format_location(loc, cwd)
        return f"{name} ({kind}) — {where}"
    rng = sym.get("range", {})
    start = rng.get("start", {})
    line = start.get("line", 0) + 1
    return f"{name} ({kind}) line {line}"


def format_diagnostic(diag: dict[str, Any]) -> str:
    """Format a single LSP Diagnostic."""
    severity_map = {1: "error", 2: "warning", 3: "info", 4: "hint"}
    sev = severity_map.get(diag.get("severity", 0), "unknown")
    msg = diag.get("message", "")
    rng = diag.get("range", {})
    start = rng.get("start", {})
    line = start.get("line", 0) + 1
    col = start.get("character", 0) + 1
    source = diag.get("source", "")
    source_str = f" [{source}]" if source else ""
    return f"  {line}:{col} {sev}{source_str}: {msg}"


def _uri_to_path(uri: str) -> str:
    """Convert a file:// URI to a filesystem path."""
    path = uri
    if path.startswith("file:///"):
        path = path[len("file:///"):]
        if sys.platform == "win32" and len(path) >= 2 and path[1] == ":":
            pass  # e.g. C:/foo
        else:
            path = "/" + path
    elif path.startswith("file://"):
        path = path[len("file://"):]
    try:
        from urllib.parse import unquote
        path = unquote(path)
    except Exception:
        pass
    return path


_SYMBOL_KIND_NAMES: dict[int, str] = {
    1: "File", 2: "Module", 3: "Namespace", 4: "Package", 5: "Class",
    6: "Method", 7: "Property", 8: "Field", 9: "Constructor", 10: "Enum",
    11: "Interface", 12: "Function", 13: "Variable", 14: "Constant",
    15: "String", 16: "Number", 17: "Boolean", 18: "Array", 19: "Object",
    20: "Key", 21: "Null", 22: "EnumMember", 23: "Struct", 24: "Event",
    25: "Operator", 26: "TypeParameter",
}


# ---------------------------------------------------------------------------
# LSPTool
# ---------------------------------------------------------------------------

class LSPTool(Tool):
    name = "LSP"
    description = (
        "Language Server Protocol operations for code intelligence — "
        "go-to-definition, find references, hover info, symbols, diagnostics, "
        "rename, and code actions."
    )
    instructions = """\
Interact with Language Server Protocol (LSP) servers to get code intelligence.

Supported operations:
- goto_definition: Find where a symbol is defined
- find_references: Find all references to a symbol
- hover: Get hover information (documentation, type info) for a symbol
- document_symbols: Get all symbols (functions, classes, variables) in a file
- workspace_symbols: Search for symbols across the workspace
- diagnostics: Get linter/type errors for a file
- rename: Rename a symbol across the codebase
- code_actions: Get available code actions at a position

Position-based operations (goto_definition, find_references, hover, rename, \
code_actions) require file, line, and column.

File-based operations (document_symbols, diagnostics) require only file.

workspace_symbols requires a query string.

Line and column are 1-based (as shown in editors).

LSP servers are auto-detected from file extension. Available servers: \
pyright/pylsp (Python), typescript-language-server (JS/TS), gopls (Go), \
rust-analyzer (Rust), clangd (C/C++).\
"""
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "The LSP operation to perform",
                    "enum": list(_OPERATIONS),
                },
                "file": {
                    "type": "string",
                    "description": "Path to the file (required for most operations)",
                },
                "line": {
                    "type": "integer",
                    "description": "Line number (1-based, required for position-based ops)",
                },
                "column": {
                    "type": "integer",
                    "description": "Column number (1-based, required for position-based ops)",
                },
                "query": {
                    "type": "string",
                    "description": "Search query (for workspace_symbols)",
                },
                "new_name": {
                    "type": "string",
                    "description": "New name for rename operation",
                },
            },
            "required": ["action"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        action: str = kwargs.get("action", "")
        if action not in _OPERATIONS:
            return f"Error: Unknown action '{action}'. Must be one of: {', '.join(_OPERATIONS)}"

        file_path: str | None = kwargs.get("file")
        line: int | None = kwargs.get("line")
        column: int | None = kwargs.get("column")
        query: str = kwargs.get("query", "")
        new_name: str = kwargs.get("new_name", "")

        # Validate required params
        if action in _POSITION_OPS:
            if not file_path:
                return f"Error: 'file' is required for {action}"
            if line is None or column is None:
                return f"Error: 'line' and 'column' are required for {action}"
        elif action in _FILE_OPS:
            if not file_path:
                return f"Error: 'file' is required for {action}"
        elif action in _QUERY_OPS:
            if not file_path:
                return f"Error: 'file' is required for {action} (used to detect language server)"

        if action == "rename" and not new_name:
            return "Error: 'new_name' is required for rename"

        # Resolve file path and detect language
        resolved_path: str | None = None
        language: str | None = None
        if file_path:
            resolved_path = str(Path(file_path).resolve())
            if not Path(resolved_path).exists():
                return f"Error: File not found: {resolved_path}"
            ext = Path(resolved_path).suffix.lower()
            language = _EXT_TO_LANGUAGE.get(ext)
            if language is None:
                return f"Error: No LSP server known for extension '{ext}'"

        if language is None:
            return "Error: Could not determine language for LSP server"

        assert resolved_path is not None
        cwd = str(Path(resolved_path).parent)
        manager = _get_manager()

        # Ensure file is open in LSP
        await manager.open_file(resolved_path, language, root_path=cwd)

        uri = Path(resolved_path).as_uri()

        try:
            if action == "goto_definition":
                return await self._goto_definition(manager, language, uri, line, column, cwd)
            elif action == "find_references":
                return await self._find_references(manager, language, uri, line, column, cwd)
            elif action == "hover":
                return await self._hover(manager, language, uri, line, column, cwd)
            elif action == "document_symbols":
                return await self._document_symbols(manager, language, uri, cwd)
            elif action == "workspace_symbols":
                return await self._workspace_symbols(manager, language, query, cwd)
            elif action == "diagnostics":
                return await self._diagnostics(manager, language, uri, resolved_path, cwd)
            elif action == "rename":
                return await self._rename(manager, language, uri, line, column, new_name, cwd)
            elif action == "code_actions":
                return await self._code_actions(manager, language, uri, line, column, cwd)
            else:
                return f"Error: Unhandled action '{action}'"
        except Exception as exc:
            return f"Error performing {action}: {exc}"

    # ------------------------------------------------------------------
    # Operation implementations
    # ------------------------------------------------------------------

    async def _goto_definition(
        self, mgr: LSPServerManager, lang: str, uri: str,
        line: int | None, col: int | None, cwd: str,
    ) -> str:
        assert line is not None and col is not None
        result = await mgr.send_request(lang, "textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": col - 1},
        }, root_path=cwd)
        if result is None:
            return "No definition found"
        locations = result if isinstance(result, list) else [result]
        if not locations:
            return "No definition found"
        lines = [format_location(_normalize_location(loc), cwd) for loc in locations]
        return f"Definition(s) found ({len(lines)}):\n" + "\n".join(lines)

    async def _find_references(
        self, mgr: LSPServerManager, lang: str, uri: str,
        line: int | None, col: int | None, cwd: str,
    ) -> str:
        assert line is not None and col is not None
        result = await mgr.send_request(lang, "textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": col - 1},
            "context": {"includeDeclaration": True},
        }, root_path=cwd)
        if not result:
            return "No references found"
        lines = [format_location(loc, cwd) for loc in result]
        unique_files = len({loc.get("uri", "") for loc in result})
        return f"References ({len(lines)} in {unique_files} file(s)):\n" + "\n".join(lines)

    async def _hover(
        self, mgr: LSPServerManager, lang: str, uri: str,
        line: int | None, col: int | None, cwd: str,
    ) -> str:
        assert line is not None and col is not None
        result = await mgr.send_request(lang, "textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": col - 1},
        }, root_path=cwd)
        if not result:
            return "No hover information available"
        contents = result.get("contents", "")
        if isinstance(contents, dict):
            return contents.get("value", str(contents))
        if isinstance(contents, list):
            parts = []
            for item in contents:
                if isinstance(item, dict):
                    parts.append(item.get("value", str(item)))
                else:
                    parts.append(str(item))
            return "\n---\n".join(parts)
        return str(contents)

    async def _document_symbols(
        self, mgr: LSPServerManager, lang: str, uri: str, cwd: str,
    ) -> str:
        result = await mgr.send_request(lang, "textDocument/documentSymbol", {
            "textDocument": {"uri": uri},
        }, root_path=cwd)
        if not result:
            return "No symbols found"
        symbols = result if isinstance(result, list) else [result]
        lines = _format_symbol_tree(symbols, cwd)
        count = _count_symbols(symbols)
        return f"Symbols ({count}):\n" + "\n".join(lines)

    async def _workspace_symbols(
        self, mgr: LSPServerManager, lang: str, query: str, cwd: str,
    ) -> str:
        result = await mgr.send_request(lang, "workspace/symbol", {
            "query": query,
        }, root_path=cwd)
        if not result:
            return f"No symbols matching '{query}'"
        symbols = result if isinstance(result, list) else [result]
        lines = [format_symbol(sym, cwd) for sym in symbols[:100]]
        header = f"Workspace symbols matching '{query}' ({len(result)} results)"
        if len(result) > 100:
            header += " — showing first 100"
        return header + ":\n" + "\n".join(lines)

    async def _diagnostics(
        self, mgr: LSPServerManager, lang: str, uri: str,
        file_path: str, cwd: str,
    ) -> str:
        # Diagnostics are pushed via notifications, not request/response.
        # As a fallback, try external linters directly.
        return _run_external_diagnostics(file_path)

    async def _rename(
        self, mgr: LSPServerManager, lang: str, uri: str,
        line: int | None, col: int | None, new_name: str, cwd: str,
    ) -> str:
        assert line is not None and col is not None
        result = await mgr.send_request(lang, "textDocument/rename", {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": col - 1},
            "newName": new_name,
        }, root_path=cwd)
        if not result:
            return "Rename failed — no response from LSP server"

        changes = result.get("changes", {})
        doc_changes = result.get("documentChanges", [])

        if changes:
            total_edits = sum(len(edits) for edits in changes.values())
            files_changed = len(changes)
            file_list = "\n".join(
                f"  {_uri_to_path(u)} ({len(edits)} edit(s))"
                for u, edits in changes.items()
            )
            return (
                f"Rename to '{new_name}': {total_edits} edit(s) across "
                f"{files_changed} file(s):\n{file_list}\n\n"
                "Note: Changes are reported but NOT applied. Use file_edit to apply."
            )

        if doc_changes:
            total_edits = sum(len(dc.get("edits", [])) for dc in doc_changes)
            return (
                f"Rename to '{new_name}': {total_edits} edit(s) across "
                f"{len(doc_changes)} document(s).\n"
                "Note: Changes are reported but NOT applied. Use file_edit to apply."
            )

        return "Rename produced no changes."

    async def _code_actions(
        self, mgr: LSPServerManager, lang: str, uri: str,
        line: int | None, col: int | None, cwd: str,
    ) -> str:
        assert line is not None and col is not None
        pos = {"line": line - 1, "character": col - 1}
        result = await mgr.send_request(lang, "textDocument/codeAction", {
            "textDocument": {"uri": uri},
            "range": {"start": pos, "end": pos},
            "context": {"diagnostics": []},
        }, root_path=cwd)
        if not result:
            return "No code actions available"
        actions = result if isinstance(result, list) else [result]
        lines = []
        for i, action in enumerate(actions, 1):
            title = action.get("title", "<untitled>")
            kind = action.get("kind", "")
            kind_str = f" [{kind}]" if kind else ""
            lines.append(f"  {i}. {title}{kind_str}")
        return f"Code actions ({len(actions)}):\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_location(loc: dict[str, Any]) -> dict[str, Any]:
    """Convert LocationLink to Location format if needed."""
    if "targetUri" in loc:
        return {
            "uri": loc["targetUri"],
            "range": loc.get("targetSelectionRange", loc.get("targetRange", {})),
        }
    return loc


def _format_symbol_tree(
    symbols: list[dict[str, Any]], cwd: str | None, indent: int = 0,
) -> list[str]:
    """Format a tree of DocumentSymbol or flat SymbolInformation list."""
    lines: list[str] = []
    prefix = "  " * indent
    for sym in symbols:
        name = sym.get("name", "<unknown>")
        kind = _SYMBOL_KIND_NAMES.get(sym.get("kind", 0), "unknown")
        rng = sym.get("range") or sym.get("location", {}).get("range", {})
        start = rng.get("start", {})
        start_line = start.get("line", 0) + 1
        lines.append(f"{prefix}{name} ({kind}) line {start_line}")
        children = sym.get("children", [])
        if children:
            lines.extend(_format_symbol_tree(children, cwd, indent + 1))
    return lines


def _count_symbols(symbols: list[dict[str, Any]]) -> int:
    """Count total symbols including nested children."""
    count = len(symbols)
    for sym in symbols:
        children = sym.get("children", [])
        if children:
            count += _count_symbols(children)
    return count


def _run_external_diagnostics(file_path: str) -> str:
    """Fallback: run an external linter and return diagnostics."""
    import subprocess

    ext = Path(file_path).suffix.lower()
    linter_cmds: dict[str, list[str]] = {
        ".py": ["ruff", "check", "--output-format=text", "--quiet"],
        ".js": ["eslint", "--no-color", "--format=compact"],
        ".ts": ["eslint", "--no-color", "--format=compact"],
        ".tsx": ["eslint", "--no-color", "--format=compact"],
        ".go": ["go", "vet"],
    }
    cmd = linter_cmds.get(ext)
    if cmd is None:
        return f"No external linter configured for '{ext}' files."

    if shutil.which(cmd[0]) is None:
        return f"Linter '{cmd[0]}' not found on PATH."

    try:
        result = subprocess.run(
            cmd + [file_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = (result.stdout or "") + (result.stderr or "")
        output = output.strip()
        if not output:
            return "No diagnostics found."
        return f"Diagnostics:\n{output}"
    except subprocess.TimeoutExpired:
        return "Error: Linter timed out."
    except Exception as exc:
        return f"Error running linter: {exc}"
