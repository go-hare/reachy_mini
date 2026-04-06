"""MCPToolWrapper — bridges MCP tools into mini-agent's Tool system.

Mirrors Claude Code's pattern:
- Tool names are namespaced as ``mcp__<server>__<tool>``
- Each discovered MCP tool becomes a full ``Tool`` instance
- Execution delegates to the :class:`McpClient` that owns it
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..tool import Tool, ToolUseContext
from .client import McpClient
from .types import McpToolInfo

logger = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    """Normalize a name for MCP namespacing — alphanumeric + underscores only."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name).strip("_")


def build_mcp_tool_name(server_name: str, tool_name: str) -> str:
    """Build a fully-qualified MCP tool name: ``mcp__<server>__<tool>``."""
    return f"mcp__{normalize_name(server_name)}__{normalize_name(tool_name)}"


def parse_mcp_tool_name(full_name: str) -> tuple[str, str] | None:
    """Extract ``(server_name, tool_name)`` from a qualified name, or None."""
    parts = full_name.split("__", 2)
    if len(parts) < 3 or parts[0] != "mcp":
        return None
    return parts[1], parts[2]


class MCPToolWrapper(Tool):
    """Wraps a single MCP tool as a mini-agent Tool.

    Created automatically by :class:`MCPConnectionManager` when tools
    are discovered from connected servers.
    """

    def __init__(
        self,
        *,
        info: McpToolInfo,
        client: McpClient,
    ) -> None:
        self._info = info
        self._client = client
        self.name = build_mcp_tool_name(info.server_name, info.name)
        self.description = self._build_description()
        self.is_read_only = False

    @property
    def server_name(self) -> str:
        return self._info.server_name

    @property
    def original_tool_name(self) -> str:
        return self._info.name

    @property
    def mcp_info(self) -> dict[str, str]:
        return {"serverName": self._info.server_name, "toolName": self._info.name}

    def _build_description(self) -> str:
        desc = self._info.description or f"MCP tool from {self._info.server_name}"
        return f"{desc} (MCP: {self._info.server_name})"

    def get_parameters_schema(self) -> dict[str, Any]:
        schema = self._info.input_schema
        if not schema:
            return {"type": "object", "properties": {}}
        return schema

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        """Call the MCP tool via the client and return the result as text."""
        if not self._client.is_connected:
            return f"Error: MCP server '{self._info.server_name}' is not connected."

        try:
            result = await self._client.call_tool(self._info.name, kwargs)
            return _format_result(result)
        except Exception as exc:
            logger.error("MCP tool '%s' failed: %s", self.name, exc)
            return f"Error calling MCP tool '{self._info.name}': {exc}"

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.get_parameters_schema(),
        }


def _format_result(result: dict[str, Any]) -> str:
    """Format an MCP tool result into a text string.

    MCP results have a ``content`` array with typed blocks (text, image, etc.)
    and an ``isError`` flag.
    """
    is_error = result.get("isError", False)
    content = result.get("content", [])

    parts: list[str] = []
    for block in content:
        block_type = block.get("type", "text")
        if block_type == "text":
            parts.append(block.get("text", ""))
        elif block_type == "image":
            parts.append(f"[Image: {block.get('mimeType', 'image/*')}]")
        elif block_type == "resource":
            resource = block.get("resource", {})
            parts.append(f"[Resource: {resource.get('uri', '?')}]\n{resource.get('text', '')}")
        else:
            parts.append(json.dumps(block, ensure_ascii=False))

    text = "\n".join(parts) if parts else json.dumps(result, ensure_ascii=False)

    if is_error:
        return f"MCP Error: {text}"
    return text


def wrap_tools(client: McpClient) -> list[MCPToolWrapper]:
    """Wrap all discovered tools from a connected client."""
    return [
        MCPToolWrapper(info=tool_info, client=client)
        for tool_info in client.connection.tools
    ]


# ── Permission integration ──────────────────────────────────────────

from ..permissions import PermissionChecker, PermissionDecision  # noqa: E402

_READ_ONLY_HINTS = frozenset({
    "get", "list", "read", "search", "find", "query", "fetch",
    "describe", "show", "info", "status", "check", "count",
    "lookup", "view", "inspect", "stat", "browse", "scan",
})


def _infer_read_only(tool: MCPToolWrapper) -> bool:
    """Heuristic: tools whose names start with read-only verbs are safe."""
    name_lower = tool.original_tool_name.lower()
    desc_lower = (tool.description or "").lower()
    for hint in _READ_ONLY_HINTS:
        if name_lower.startswith(hint) or name_lower.startswith(f"{hint}_"):
            return True
    if any(f"read-only" in desc_lower or "does not modify" in desc_lower
           for _ in [None]):
        return True
    return False


def wrap_with_permissions(
    mcp_tool: MCPToolWrapper,
    permission_checker: PermissionChecker,
    *,
    infer_read_only: bool = True,
) -> MCPToolWrapper:
    """Apply the permission system to an MCP tool.

    If ``infer_read_only`` is True and the tool name/description suggests
    it's read-only, the wrapper's ``is_read_only`` flag is set so the
    permission pipeline's stage 3 (safe allowlist) can auto-allow it.
    """
    if infer_read_only and _infer_read_only(mcp_tool):
        mcp_tool.is_read_only = True
    mcp_tool._permission_checker = permission_checker  # type: ignore[attr-defined]
    return mcp_tool


# ── Result caching ──────────────────────────────────────────────────

import hashlib  # noqa: E402
import time as _time  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402


@dataclass(slots=True)
class _CacheEntry:
    result: str
    expires_at: float


class MCPResultCache:
    """TTL-based cache for read-only MCP tool results.

    Only caches calls to tools flagged ``is_read_only=True``.
    Claude Code doesn't cache MCP results, but this is a natural
    optimisation for tools like ``list_resources`` that are called
    repeatedly with identical arguments.
    """

    def __init__(self, ttl: float = 60.0) -> None:
        self._ttl = ttl
        self._store: dict[str, _CacheEntry] = {}

    @staticmethod
    def _key(tool_name: str, kwargs: dict[str, Any]) -> str:
        blob = json.dumps({"t": tool_name, "a": kwargs}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()

    def get(self, tool_name: str, kwargs: dict[str, Any]) -> str | None:
        key = self._key(tool_name, kwargs)
        entry = self._store.get(key)
        if entry is None:
            return None
        if _time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        return entry.result

    def put(self, tool_name: str, kwargs: dict[str, Any], result: str) -> None:
        key = self._key(tool_name, kwargs)
        self._store[key] = _CacheEntry(
            result=result,
            expires_at=_time.monotonic() + self._ttl,
        )

    def invalidate(self, tool_name: str | None = None) -> None:
        """Invalidate entries for *tool_name*, or all if None."""
        if tool_name is None:
            self._store.clear()
            return
        prefix = hashlib.sha256(
            json.dumps({"t": tool_name}, sort_keys=True).encode()
        ).hexdigest()[:8]
        # Full invalidation by tool name requires scanning
        self._store = {
            k: v for k, v in self._store.items()
            if not k.startswith(prefix)
        }

    def invalidate_all(self) -> None:
        self._store.clear()

    def _evict_expired(self) -> None:
        now = _time.monotonic()
        expired = [k for k, v in self._store.items() if now > v.expires_at]
        for k in expired:
            del self._store[k]


# ── Timeout handling ────────────────────────────────────────────────

import asyncio  # noqa: E402

DEFAULT_TOOL_TIMEOUT = 30.0
LONG_RUNNING_TOOL_TIMEOUT = 120.0

_LONG_RUNNING_HINTS = frozenset({
    "execute", "run", "build", "compile", "deploy", "install",
    "generate", "process", "analyze", "download", "upload",
    "train", "benchmark", "migrate", "transform", "convert",
})


def get_tool_timeout(tool: MCPToolWrapper, *, default: float = DEFAULT_TOOL_TIMEOUT) -> float:
    """Determine the timeout for an MCP tool call.

    Tools whose names suggest long-running operations get a 120s timeout;
    others default to 30s.  Override via ``tool._custom_timeout``.
    """
    custom = getattr(tool, "_custom_timeout", None)
    if custom is not None:
        return float(custom)

    name_lower = tool.original_tool_name.lower()
    for hint in _LONG_RUNNING_HINTS:
        if hint in name_lower:
            return LONG_RUNNING_TOOL_TIMEOUT
    return default


async def execute_with_timeout(
    tool: MCPToolWrapper,
    *,
    context: ToolUseContext,
    timeout: float | None = None,
    cache: MCPResultCache | None = None,
    **kwargs: Any,
) -> str:
    """Execute an MCP tool with timeout, optional caching, and a
    human-friendly error message on expiry.
    """
    effective_timeout = timeout or get_tool_timeout(tool)

    if cache and tool.is_read_only:
        cached = cache.get(tool.name, kwargs)
        if cached is not None:
            return cached

    try:
        result = await asyncio.wait_for(
            tool.execute(context=context, **kwargs),
            timeout=effective_timeout,
        )
    except asyncio.TimeoutError:
        return (
            f"Error: MCP tool '{tool.original_tool_name}' timed out "
            f"after {effective_timeout:.0f}s. The tool may be slow or the "
            f"server may be unresponsive."
        )

    if cache and tool.is_read_only:
        cache.put(tool.name, kwargs, result)

    return result
