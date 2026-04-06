"""Built-in HTTP server for the mini-agent.

Ported from Claude Code's ``server/`` subsystem:
- ``AgentHTTPServer`` — REST API with query, tool, session endpoints
- ``DirectConnectManager`` — multi-session isolation and lifecycle
"""

from .http_server import AgentHTTPServer, DirectConnectManager

__all__ = [
    "AgentHTTPServer",
    "DirectConnectManager",
]
