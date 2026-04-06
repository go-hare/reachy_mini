"""McpAuthTool — update MCP server auth state in the local config."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..paths import mini_agent_path
from ..tool import Tool, ToolUseContext
from ..mcp.oauth import (
    clear_oauth_state,
    get_flow_state,
    get_oauth_state,
    start_oauth_flow,
)


def _mcp_config_path() -> Path:
    return mini_agent_path("mcp_servers.json")


def _load_mcp_json(path: Path | None = None) -> dict[str, Any]:
    target = path or _mcp_config_path()
    if not target.exists():
        return {"mcpServers": {}}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {"mcpServers": {}}
    if not isinstance(data, dict):
        return {"mcpServers": {}}
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        data["mcpServers"] = {}
    return data


def _save_mcp_json(data: dict[str, Any], path: Path | None = None) -> Path:
    target = path or _mcp_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


class McpAuthTool(Tool):
    """Persist MCP auth headers or tokens for a configured server."""

    name = "McpAuth"
    description = "Manage local authentication headers for MCP servers."
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "status",
                        "set_header",
                        "remove_header",
                        "set_bearer",
                        "clear_auth",
                        "start_oauth",
                        "oauth_status",
                        "clear_oauth",
                    ],
                    "description": "Auth action to perform.",
                },
                "server_name": {
                    "type": "string",
                    "description": "Configured MCP server name.",
                },
                "header_name": {
                    "type": "string",
                    "description": "HTTP header name for set/remove header.",
                },
                "header_value": {
                    "type": "string",
                    "description": "HTTP header value for set_header.",
                },
                "token": {
                    "type": "string",
                    "description": "Bearer token for set_bearer.",
                },
                "flow_id": {
                    "type": "string",
                    "description": "OAuth flow ID for oauth_status.",
                },
                "open_browser": {
                    "type": "boolean",
                    "description": "Open the auth URL in a browser for start_oauth.",
                    "default": False,
                },
            },
            "required": ["action", "server_name"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        action = kwargs["action"]
        server_name = kwargs["server_name"]
        data = _load_mcp_json()
        servers = data.setdefault("mcpServers", {})
        server = servers.get(server_name)
        if not isinstance(server, dict):
            return f"MCP server not found: {server_name}"

        headers = server.setdefault("headers", {})
        if not isinstance(headers, dict):
            headers = {}
            server["headers"] = headers

        if action == "status":
            masked = {
                key: ("set" if value else "")
                for key, value in headers.items()
            }
            oauth_state = get_oauth_state(server_name)
            return json.dumps(
                {
                    "server_name": server_name,
                    "transport": server.get("type", "stdio"),
                    "url": server.get("url", ""),
                    "headers": masked,
                    "oauth": {
                        "configured": bool(oauth_state),
                        "has_tokens": bool(oauth_state.get("tokens")),
                        "has_client_info": bool(oauth_state.get("client_info")),
                    },
                },
                indent=2,
                ensure_ascii=False,
            )

        if action == "set_header":
            header_name = str(kwargs.get("header_name", "")).strip()
            if not header_name:
                return "header_name is required for set_header"
            headers[header_name] = str(kwargs.get("header_value", ""))
            path = _save_mcp_json(data)
            return f"Saved MCP header for {server_name}: {header_name}\nConfig updated: {path}"

        if action == "remove_header":
            header_name = str(kwargs.get("header_name", "")).strip()
            if not header_name:
                return "header_name is required for remove_header"
            headers.pop(header_name, None)
            path = _save_mcp_json(data)
            return f"Removed MCP header for {server_name}: {header_name}\nConfig updated: {path}"

        if action == "set_bearer":
            token = str(kwargs.get("token", "")).strip()
            if not token:
                return "token is required for set_bearer"
            headers["Authorization"] = f"Bearer {token}"
            path = _save_mcp_json(data)
            return f"Saved bearer auth for {server_name}\nConfig updated: {path}"

        if action == "clear_auth":
            headers.clear()
            path = _save_mcp_json(data)
            return f"Cleared MCP auth headers for {server_name}\nConfig updated: {path}"

        if action == "start_oauth":
            server_type = server.get("type", "stdio")
            server_url = str(server.get("url", "")).strip()
            if server_type not in {"http", "sse"} or not server_url:
                return f"MCP server '{server_name}' does not support HTTP/SSE OAuth flow."
            flow = await start_oauth_flow(
                server_name=server_name,
                server_url=server_url,
                open_browser=bool(kwargs.get("open_browser", False)),
            )
            return json.dumps(
                {
                    "flow_id": flow.flow_id,
                    "status": flow.status,
                    "auth_url": flow.auth_url,
                    "redirect_uri": flow.redirect_uri,
                    "error": flow.error,
                },
                indent=2,
                ensure_ascii=False,
            )

        if action == "oauth_status":
            flow_id = str(kwargs.get("flow_id", "")).strip()
            if not flow_id:
                return "flow_id is required for oauth_status"
            flow = get_flow_state(flow_id)
            if flow is None:
                return f"OAuth flow not found: {flow_id}"
            return json.dumps(
                {
                    "flow_id": flow.flow_id,
                    "server_name": flow.server_name,
                    "status": flow.status,
                    "auth_url": flow.auth_url,
                    "redirect_uri": flow.redirect_uri,
                    "error": flow.error,
                    "completed_at": flow.completed_at,
                },
                indent=2,
                ensure_ascii=False,
            )

        if action == "clear_oauth":
            cleared = clear_oauth_state(server_name)
            return (
                f"Cleared OAuth state for {server_name}"
                if cleared
                else f"No OAuth state found for {server_name}"
            )

        return f"Unsupported action: {action}"
