"""MCPConnectionManager — manages multiple MCP server connections.

Mirrors the role of Claude Code's ``MCPConnectionManager`` / ``useManageMCPConnections``:
- Accepts a config dict of ``{name: McpServerConfig}``
- Connects to all servers concurrently
- Aggregates discovered tools across all connections
- Supports reconnect, toggle (enable/disable), and hot-reload
- Injects MCP server instructions into the system prompt
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from ..paths import mini_agent_path
from ..attachments import Attachment, AttachmentSource
from ..tool import Tool
from .client import McpClient
from .tool_wrapper import MCPToolWrapper, wrap_tools
from .types import (
    ConnectionStatus,
    McpConnection,
    McpHttpConfig,
    McpServerConfig,
    McpStdioConfig,
)

logger = logging.getLogger(__name__)


class MCPConnectionManager:
    """Lifecycle manager for multiple MCP servers.

    Usage::

        config = {
            "filesystem": McpStdioConfig(command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]),
            "weather": McpHttpConfig(url="http://localhost:3001/mcp"),
        }
        manager = MCPConnectionManager(config)
        await manager.connect_all()

        tools = manager.get_tools()       # All tools from all servers
        connections = manager.connections  # Name → McpConnection

        await manager.close_all()
    """

    _default_instance: MCPConnectionManager | None = None

    def __init__(self, config: dict[str, McpServerConfig] | None = None) -> None:
        self._config = config or {}
        self._clients: dict[str, McpClient] = {}
        self._logging_callback_factory: Any = None

    @property
    def connections(self) -> dict[str, McpConnection]:
        """All connection states, keyed by server name."""
        return {name: client.connection for name, client in self._clients.items()}

    @property
    def clients(self) -> dict[str, McpClient]:
        """Connected MCP clients keyed by server name."""
        return dict(self._clients)

    @property
    def connected_count(self) -> int:
        return sum(1 for c in self._clients.values() if c.is_connected)

    # ── Bulk lifecycle ──────────────────────────────────────────────

    async def connect_all(self) -> dict[str, McpConnection]:
        """Connect to all configured servers concurrently."""
        tasks = []
        for name, cfg in self._config.items():
            client = McpClient(name, cfg)
            if self._logging_callback_factory is not None:
                client.set_logging_callback(self._logging_callback_factory(name))
            self._clients[name] = client
            tasks.append(client.connect())

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        connected = self.connected_count
        total = len(self._clients)
        logger.info("MCP: %d/%d servers connected", connected, total)
        return self.connections

    async def close_all(self) -> None:
        """Close all server connections."""
        tasks = [client.close() for client in self._clients.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._clients.clear()

    async def __aenter__(self) -> MCPConnectionManager:
        await self.connect_all()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close_all()

    # ── Query helpers ────────────────────────────────────────────────

    def get_connected_servers(self) -> dict[str, McpConnection]:
        """Return only the servers that are currently connected."""
        return {
            name: client.connection
            for name, client in self._clients.items()
            if client.is_connected
        }

    def get_server_status(self, name: str) -> ConnectionStatus | None:
        """Return the connection status of a specific server, or ``None``."""
        client = self._clients.get(name)
        if client is None:
            return None
        return client.connection.status

    def get_client(self, name: str) -> McpClient | None:
        """Return a specific MCP client by server name."""
        return self._clients.get(name)

    # ── Per-server operations ───────────────────────────────────────

    async def reconnect(self, server_name: str) -> McpConnection | None:
        """Reconnect a specific server (close then re-open)."""
        old = self._clients.pop(server_name, None)
        if old is not None:
            await old.close()

        cfg = self._config.get(server_name)
        if cfg is None:
            logger.warning("No config for server '%s'", server_name)
            return None

        client = McpClient(server_name, cfg)
        if self._logging_callback_factory is not None:
            client.set_logging_callback(self._logging_callback_factory(server_name))
        self._clients[server_name] = client
        return await client.connect()

    async def toggle(self, server_name: str) -> McpConnection | None:
        """Toggle a server between connected and disabled."""
        client = self._clients.get(server_name)
        if client is None:
            return await self.reconnect(server_name)

        if client.is_connected:
            await client.close()
            return client.connection
        else:
            return await self.reconnect(server_name)

    async def add_server(self, name: str, config: McpServerConfig) -> McpConnection:
        """Hot-add a new server at runtime."""
        self._config[name] = config
        old = self._clients.pop(name, None)
        if old is not None:
            await old.close()

        client = McpClient(name, config)
        if self._logging_callback_factory is not None:
            client.set_logging_callback(self._logging_callback_factory(name))
        self._clients[name] = client
        return await client.connect()

    def set_logging_callback_factory(self, factory: Any) -> None:
        """Install a callback factory used for MCP logging notifications."""
        self._logging_callback_factory = factory
        for name, client in self._clients.items():
            client.set_logging_callback(factory(name))

    async def remove_server(self, name: str) -> None:
        """Remove and disconnect a server."""
        self._config.pop(name, None)
        client = self._clients.pop(name, None)
        if client is not None:
            await client.close()

    # ── Tool aggregation ────────────────────────────────────────────

    def get_tools(self) -> list[MCPToolWrapper]:
        """Get all MCP tools from all connected servers."""
        tools: list[MCPToolWrapper] = []
        for client in self._clients.values():
            if client.is_connected:
                tools.extend(wrap_tools(client))
        return tools

    def get_tool(self, full_name: str) -> MCPToolWrapper | None:
        """Find a specific MCP tool by its qualified name."""
        for tool in self.get_tools():
            if tool.name == full_name:
                return tool
        return None

    # ── Instructions for system prompt ──────────────────────────────

    def get_instructions(self) -> str | None:
        """Aggregate server instructions for the system prompt."""
        parts: list[str] = []
        for name, client in self._clients.items():
            conn = client.connection
            if conn.status == ConnectionStatus.CONNECTED and conn.instructions:
                parts.append(f"## MCP Server: {name}\n{conn.instructions}")
        return "\n\n".join(parts) if parts else None

    # ── Status summary ──────────────────────────────────────────────

    def status_summary(self) -> str:
        """Human-readable summary of all connections."""
        lines: list[str] = []
        for name, client in self._clients.items():
            conn = client.connection
            status = conn.status.value
            tool_count = len(conn.tools)
            if conn.error:
                lines.append(f"  {name}: {status} ({conn.error})")
            else:
                lines.append(f"  {name}: {status} ({tool_count} tools)")
        if not lines:
            return "No MCP servers configured."
        return "MCP Servers:\n" + "\n".join(lines)


class MCPInstructionsSource(AttachmentSource):
    """Inject MCP server instructions into the system prompt."""

    def __init__(self, manager: MCPConnectionManager) -> None:
        self._manager = manager

    async def get_attachments(self, context: dict[str, Any]) -> list[Attachment]:
        instructions = self._manager.get_instructions()
        if not instructions:
            return []
        return [
            Attachment(
                type="mcp_instructions",
                content=instructions,
                metadata={"server_count": self._manager.connected_count},
            )
        ]


# ── Config loading ──────────────────────────────────────────────────

def load_config(path: Path) -> dict[str, McpServerConfig]:
    """Load MCP server config from a JSON file.

    Expected format (compatible with Claude Code's ``mcp.json``)::

        {
            "mcpServers": {
                "server-name": {
                    "command": "npx",
                    "args": ["-y", "@some/server"],
                    "env": {}
                }
            }
        }
    """
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to parse MCP config from %s", path)
        return {}

    servers = data.get("mcpServers", {})
    config: dict[str, McpServerConfig] = {}

    for name, spec in servers.items():
        transport = spec.get("type", "stdio")
        if transport == "stdio":
            config[name] = McpStdioConfig(
                command=spec.get("command", ""),
                args=spec.get("args", []),
                env=spec.get("env", {}),
            )
        elif transport in ("sse", "http"):
            config[name] = McpHttpConfig(
                url=spec.get("url", ""),
                headers=spec.get("headers", {}),
            )
        else:
            logger.warning("Unsupported MCP transport '%s' for server '%s'", transport, name)

    return config


# ── Auto-discovery ──────────────────────────────────────────────────

_DEFAULT_MCP_SERVERS_PATH = mini_agent_path("mcp_servers.json")


def auto_discover_servers(
    path: Path | None = None,
) -> dict[str, McpServerConfig]:
    """Discover MCP servers from ``~/.mini_agent/mcp_servers.json``.

    Falls back gracefully if the file doesn't exist.
    """
    config_path = path or _DEFAULT_MCP_SERVERS_PATH
    return load_config(config_path)


def set_mcp_manager(manager: MCPConnectionManager | None) -> None:
    """Set the process-global MCP manager used by MCP resource tools."""
    MCPConnectionManager._default_instance = manager


def get_mcp_manager() -> MCPConnectionManager | None:
    """Get the process-global MCP manager used by MCP resource tools."""
    return MCPConnectionManager._default_instance


# ── Server health monitoring ────────────────────────────────────────

import time as _time  # noqa: E402

from .types import (
    MCPConnectionState,
    MCPServerStatusInfo,
    ServerHealthStatus,
)


class ServerHealthMonitor:
    """Periodic health-check loop for connected MCP servers.

    Mirrors Claude Code's reconnect-on-close semantics: when a health
    check fails, the server is marked degraded/down and optionally
    restarted via ``MCPConnectionManager.reconnect``.
    """

    def __init__(
        self,
        manager: MCPConnectionManager,
        *,
        interval: float = 30.0,
        degraded_threshold_ms: float = 5000.0,
    ) -> None:
        self._manager = manager
        self._interval = interval
        self._degraded_threshold_ms = degraded_threshold_ms
        self._statuses: dict[str, MCPServerStatusInfo] = {}
        self._task: asyncio.Task[None] | None = None

    @property
    def statuses(self) -> dict[str, MCPServerStatusInfo]:
        return dict(self._statuses)

    def get_server_health(self, name: str) -> ServerHealthStatus:
        info = self._statuses.get(name)
        return info.health if info else ServerHealthStatus.DOWN

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        try:
            while True:
                await self._check_all()
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass

    async def _check_all(self) -> None:
        for name, client in list(self._manager._clients.items()):
            status = self._statuses.setdefault(
                name, MCPServerStatusInfo(name=name),
            )
            if not client.is_connected:
                status.state = MCPConnectionState.DISCONNECTED
                status.health = ServerHealthStatus.DOWN
                continue

            try:
                start = _time.monotonic()
                await asyncio.wait_for(client.list_tools(), timeout=10.0)
                latency_ms = (_time.monotonic() - start) * 1000

                status.state = MCPConnectionState.CONNECTED
                status.last_latency_ms = latency_ms
                status.last_health_check = _time.time()
                status.tool_count = len(client.connection.tools)
                status.resource_count = len(client.connection.resources)

                if latency_ms > self._degraded_threshold_ms:
                    status.health = ServerHealthStatus.DEGRADED
                else:
                    status.health = ServerHealthStatus.HEALTHY
                    status.last_error = None

            except Exception as exc:
                status.health = ServerHealthStatus.DOWN
                status.last_error = str(exc)
                status.last_health_check = _time.time()
                logger.debug("Health check failed for '%s': %s", name, exc)

    async def check_and_restart_unhealthy(self) -> list[str]:
        """Restart servers whose health is DOWN. Returns restarted names."""
        restarted: list[str] = []
        for name, status in list(self._statuses.items()):
            if status.health == ServerHealthStatus.DOWN:
                try:
                    conn = await self._manager.reconnect(name)
                    if conn and conn.status == ConnectionStatus.CONNECTED:
                        status.health = ServerHealthStatus.HEALTHY
                        status.state = MCPConnectionState.CONNECTED
                        status.last_error = None
                        restarted.append(name)
                except Exception as exc:
                    logger.debug("Restart failed for '%s': %s", name, exc)
        return restarted


# ── Tool deduplication ──────────────────────────────────────────────

from .tool_wrapper import MCPToolWrapper, build_mcp_tool_name  # noqa: E402


def deduplicate_tools(
    tools: list[MCPToolWrapper],
    *,
    priority_servers: list[str] | None = None,
) -> list[MCPToolWrapper]:
    """Deduplicate MCP tools from multiple servers.

    When multiple servers expose the same ``original_tool_name``, the
    server listed earlier in *priority_servers* wins.  Other duplicates
    are namespaced as ``server_name.tool_name`` to avoid silent
    shadowing — similar to Claude Code's ``getMcpPrefix`` namespacing.

    Tools from non-conflicting servers are returned as-is.
    """
    priority = {name: i for i, name in enumerate(priority_servers or [])}

    by_original: dict[str, list[MCPToolWrapper]] = {}
    for tool in tools:
        by_original.setdefault(tool.original_tool_name, []).append(tool)

    result: list[MCPToolWrapper] = []
    for original_name, group in by_original.items():
        if len(group) == 1:
            result.append(group[0])
            continue

        group.sort(key=lambda t: priority.get(t.server_name, 999))
        result.append(group[0])

        for dup in group[1:]:
            logger.debug(
                "Tool '%s' from '%s' shadowed by '%s' — still available as '%s'",
                original_name, dup.server_name,
                group[0].server_name, dup.name,
            )
            result.append(dup)

    return result
