"""MCP data types — mirrors Claude Code's ``services/mcp/types.ts``."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TransportType(str, Enum):
    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"


class ConnectionStatus(str, Enum):
    CONNECTED = "connected"
    FAILED = "failed"
    PENDING = "pending"
    DISABLED = "disabled"


@dataclass(slots=True)
class McpStdioConfig:
    """Config for a stdio-based MCP server."""
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: TransportType = TransportType.STDIO


@dataclass(slots=True)
class McpHttpConfig:
    """Config for an HTTP/SSE-based MCP server."""
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    transport: TransportType = TransportType.HTTP


McpServerConfig = McpStdioConfig | McpHttpConfig


@dataclass(slots=True)
class McpToolInfo:
    """Discovered tool from an MCP server."""
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""


@dataclass(slots=True)
class McpResourceInfo:
    """Discovered resource from an MCP server."""
    uri: str
    name: str
    description: str = ""
    mime_type: str = ""
    server_name: str = ""


@dataclass
class McpConnection:
    """Runtime state of an MCP server connection."""
    name: str
    config: McpServerConfig
    status: ConnectionStatus = ConnectionStatus.PENDING
    tools: list[McpToolInfo] = field(default_factory=list)
    resources: list[McpResourceInfo] = field(default_factory=list)
    error: str | None = None
    server_info: dict[str, str] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    instructions: str | None = None


# ── Enhanced types (connection lifecycle & monitoring) ────────────────


class MCPConnectionState(str, Enum):
    """Fine-grained connection state with reconnect tracking.

    Mirrors Claude Code's ``PendingMCPServer.reconnectAttempt`` and the
    ``onclose`` → exponential-backoff reconnect loop in
    ``useManageMCPConnections.ts``.
    """
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    RECONNECTING = "reconnecting"


class ServerHealthStatus(str, Enum):
    """Aggregate health derived from periodic pings."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(slots=True)
class MCPServerStatusInfo:
    """Snapshot of a server's runtime status.

    Combines Claude Code's ``ConnectedMCPServer`` fields (capabilities,
    serverInfo, instructions) with connection-time bookkeeping used by
    the reconnection loop (``reconnectAttempt``, ``maxReconnectAttempts``).
    """
    name: str
    state: MCPConnectionState = MCPConnectionState.DISCONNECTED
    health: ServerHealthStatus = ServerHealthStatus.DOWN
    connected_since: float | None = None
    last_error: str | None = None
    last_health_check: float | None = None
    last_latency_ms: float | None = None
    tool_count: int = 0
    resource_count: int = 0
    reconnect_attempt: int = 0
    max_reconnect_attempts: int = 5


@dataclass(slots=True)
class MCPToolMetadata:
    """Extended tool metadata for caching and timeout decisions.

    ``is_read_only`` drives the permission-system shortcut (stage 3 in
    ``PermissionPipeline``). ``estimated_duration`` lets ``MCPToolWrapper``
    pick per-tool timeouts.
    """
    server_name: str
    tool_name: str
    qualified_name: str = ""
    is_read_only: bool = False
    estimated_duration: str = "short"  # "short" | "medium" | "long"


@dataclass(slots=True)
class MCPServerConfig:
    """Unified config for an MCP server (enhanced).

    Wraps the transport-specific ``McpStdioConfig`` / ``McpHttpConfig``
    with operational knobs: ``timeout``, ``auto_restart``, and
    ``health_check_interval`` mirroring the reconnect constants in
    ``useManageMCPConnections.ts`` (``MAX_RECONNECT_ATTEMPTS``,
    ``INITIAL_BACKOFF_MS``, ``MAX_BACKOFF_MS``).
    """
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    transport: TransportType = TransportType.STDIO
    timeout: float = 30.0
    auto_restart: bool = True
    health_check_interval: float = 30.0
    max_reconnect_attempts: int = 5

    def to_transport_config(self) -> McpServerConfig:
        """Convert to the transport-specific config for ``McpClient``."""
        if self.transport in (TransportType.SSE, TransportType.HTTP):
            return McpHttpConfig(
                url=self.url,
                headers=self.headers,
                transport=self.transport,
            )
        return McpStdioConfig(
            command=self.command,
            args=self.args,
            env=self.env,
            transport=self.transport,
        )
