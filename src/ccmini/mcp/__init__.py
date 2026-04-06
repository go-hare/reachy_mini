"""MCP (Model Context Protocol) client integration.

Connects to MCP servers via stdio or HTTP, discovers tools and resources,
and bridges them into mini-agent's Tool system.

Architecture:
- ``types``: Data models (McpServerConfig, McpConnection, McpToolInfo)
- ``client``: Low-level JSON-RPC client (stdio + HTTP transport)
- ``tool_wrapper``: Bridges MCP tools as mini-agent Tool instances
- ``manager``: Multi-server connection lifecycle + tool aggregation
"""

from .types import (
    ConnectionStatus,
    McpConnection,
    McpHttpConfig,
    McpResourceInfo,
    McpServerConfig,
    McpStdioConfig,
    McpToolInfo,
    TransportType,
    MCPConnectionState,
    MCPServerConfig as MCPServerConfigEnhanced,
    MCPServerStatusInfo,
    MCPToolMetadata,
    ServerHealthStatus,
)
from .client import (
    McpClient,
    McpError,
    MCPClientError,
    MCPConnectionError,
    MCPConnectionLifecycle,
    MCPProtocolError,
    MCPTimeoutError,
    retry_with_backoff,
)
from .tool_wrapper import (
    MCPToolWrapper,
    MCPResultCache,
    build_mcp_tool_name,
    execute_with_timeout,
    get_tool_timeout,
    normalize_name,
    parse_mcp_tool_name,
    wrap_tools,
    wrap_with_permissions,
)
from .manager import (
    MCPConnectionManager,
    MCPInstructionsSource,
    ServerHealthMonitor,
    auto_discover_servers,
    deduplicate_tools,
    load_config,
)
from .skill_bridge import (
    MCPSkillBuilders,
    build_mcp_skill_command,
    fetch_mcp_skills_for_client,
    get_mcp_skill_builders,
    is_registered as is_skill_bridge_registered,
    register_mcp_skill_builders,
)

__all__ = [
    # types
    "ConnectionStatus",
    "MCPConnectionState",
    "MCPServerConfigEnhanced",
    "MCPServerStatusInfo",
    "MCPToolMetadata",
    "McpConnection",
    "McpHttpConfig",
    "McpResourceInfo",
    "McpServerConfig",
    "McpStdioConfig",
    "McpToolInfo",
    "ServerHealthStatus",
    "TransportType",
    # client
    "MCPClientError",
    "MCPConnectionError",
    "MCPConnectionLifecycle",
    "MCPProtocolError",
    "MCPTimeoutError",
    "McpClient",
    "McpError",
    "retry_with_backoff",
    # tool_wrapper
    "MCPResultCache",
    "MCPToolWrapper",
    "build_mcp_tool_name",
    "execute_with_timeout",
    "get_tool_timeout",
    "normalize_name",
    "parse_mcp_tool_name",
    "wrap_tools",
    "wrap_with_permissions",
    # manager
    "MCPConnectionManager",
    "MCPInstructionsSource",
    "ServerHealthMonitor",
    "auto_discover_servers",
    "deduplicate_tools",
    "load_config",
    # skill_bridge
    "MCPSkillBuilders",
    "build_mcp_skill_command",
    "fetch_mcp_skills_for_client",
    "get_mcp_skill_builders",
    "is_skill_bridge_registered",
    "register_mcp_skill_builders",
]
