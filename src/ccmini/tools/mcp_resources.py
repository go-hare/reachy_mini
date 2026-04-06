"""MCP Resource Tools — list and read resources from MCP servers.

Provides two tools for the agent to interact with MCP server resources:
- ``ListMCPResourcesTool`` — enumerate available resources on a server
- ``ReadMCPResourceTool`` — read a specific resource by URI

Resource listings are cached with a configurable TTL (default 60s) so
repeated enumeration doesn't re-query the MCP server every turn.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..tool import Tool, ToolUseContext

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_TTL = 60.0


# ── Cache ───────────────────────────────────────────────────────────

@dataclass(slots=True)
class _CacheEntry:
    data: Any
    expires_at: float


class _ResourceCache:
    """TTL cache for MCP resource listings."""

    def __init__(self, ttl: float = _DEFAULT_CACHE_TTL) -> None:
        self._ttl = ttl
        self._store: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            del self._store[key]
            return None
        return entry.data

    def put(self, key: str, data: Any) -> None:
        self._store[key] = _CacheEntry(
            data=data, expires_at=time.time() + self._ttl,
        )

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._store.clear()
        else:
            self._store.pop(key, None)




def _resource_to_dict(resource: Any) -> dict[str, Any]:
    if isinstance(resource, dict):
        return dict(resource)
    return {
        "name": getattr(resource, "name", "") or "",
        "uri": getattr(resource, "uri", "") or "",
        "description": getattr(resource, "description", "") or "",
        "mime_type": getattr(resource, "mime_type", "") or "",
        "server_name": getattr(resource, "server_name", "") or "",
    }


# ── Formatting ──────────────────────────────────────────────────────

def _format_resource_table(resources: list[dict[str, Any]]) -> str:
    """Format a list of resources as a table."""
    if not resources:
        return "(no resources available)"

    name_w = max(len(r.get("name", "")) for r in resources)
    name_w = max(name_w, 4)
    uri_w = max(len(r.get("uri", "")) for r in resources)
    uri_w = max(uri_w, 3)

    lines = [
        f"{'Name':<{name_w}}  {'URI':<{uri_w}}  Description",
        f"{'-' * name_w}  {'-' * uri_w}  {'-' * 20}",
    ]
    for r in resources:
        name = r.get("name", "")
        uri = r.get("uri", "")
        desc = r.get("description", "")
        lines.append(f"{name:<{name_w}}  {uri:<{uri_w}}  {desc}")

    return "\n".join(lines)


def _expand_template(template: str, params: dict[str, str]) -> str:
    """Expand a URI template like ``file:///{path}`` with parameters."""
    result = template
    for key, value in params.items():
        result = result.replace(f"{{{key}}}", value)
    return result


# ── MCP manager accessor ───────────────────────────────────────────

def _get_mcp_manager() -> Any:
    """Lazily import the MCP manager to avoid circular imports."""
    try:
        from ..mcp.manager import get_mcp_manager
        return get_mcp_manager()
    except ImportError:
        return None


# ── Tools ───────────────────────────────────────────────────────────

class ListMCPResourcesTool(Tool):
    name = "ListMcpResourcesTool"
    description = (
        "List available resources from an MCP server. Returns a table of "
        "resource names, URIs, and descriptions."
    )
    instructions = """\
List all resources exposed by an MCP server. Use the server_name parameter \
to specify which MCP server to query.

If no server_name is given, lists resources from all connected MCP servers.

Results are cached for 60 seconds. To force a refresh, set refresh=true.\
"""
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "MCP server name to query (optional — lists all if omitted)",
                },
                "refresh": {
                    "type": "boolean",
                    "description": "Force refresh cached resource list",
                },
            },
            "required": [],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        server_name: str = kwargs.get("server_name", "")
        refresh: bool = kwargs.get("refresh", False)

        manager = _get_mcp_manager()
        if manager is None:
            return "Error: MCP manager not available"

        cache_key = f"resources:{server_name or '__all__'}"
        if not refresh:
            cached = _cache.get(cache_key)
            if cached is not None:
                return _format_resource_table(cached)

        try:
            if server_name:
                client = manager.get_client(server_name)
                if client is None:
                    return f"Error: MCP server '{server_name}' not found"
                resources = [_resource_to_dict(r) for r in await client.list_resources()]
            else:
                resources = []
                for name, client in manager.clients.items():
                    try:
                        server_resources = []
                        for resource in await client.list_resources():
                            item = _resource_to_dict(resource)
                            item["server"] = name
                            server_resources.append(item)
                        resources.extend(server_resources)
                    except Exception as exc:
                        logger.debug("Failed to list resources from %s: %s", name, exc)

            _cache.put(cache_key, resources)
            return _format_resource_table(resources)
        except Exception as exc:
            return f"Error listing resources: {exc}"


class ReadMCPResourceTool(Tool):
    name = "ReadMcpResourceTool"
    description = (
        "Read the contents of a specific MCP server resource by URI."
    )
    instructions = """\
Read a resource from an MCP server by its URI. You must specify the \
server_name and resource_uri.

For resources with URI templates (parameterised URIs like \
``file:///{path}``), provide the template_params as a JSON object.\
"""
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "server_name": {
                    "type": "string",
                    "description": "MCP server name",
                },
                "resource_uri": {
                    "type": "string",
                    "description": "Resource URI to read",
                },
                "template_params": {
                    "type": "object",
                    "description": "Parameters for URI template expansion",
                },
            },
            "required": ["server_name", "resource_uri"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        server_name: str = kwargs["server_name"]
        resource_uri: str = kwargs["resource_uri"]
        template_params: dict[str, str] = kwargs.get("template_params", {})

        manager = _get_mcp_manager()
        if manager is None:
            return "Error: MCP manager not available"

        client = manager.get_client(server_name)
        if client is None:
            return f"Error: MCP server '{server_name}' not found"

        if template_params:
            resource_uri = _expand_template(resource_uri, template_params)

        try:
            result = await client.read_resource(resource_uri)
            if isinstance(result, str):
                return result
            if isinstance(result, dict):
                import json
                return json.dumps(result, indent=2, ensure_ascii=False)
            return str(result)
        except Exception as exc:
            return f"Error reading resource '{resource_uri}': {exc}"
