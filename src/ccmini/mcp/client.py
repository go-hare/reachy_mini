"""MCP client — connects to MCP servers using the official ``mcp`` Python SDK.

Wraps ``mcp.ClientSession`` with our ``McpConnection`` data model so the
rest of mini-agent can stay SDK-agnostic.

Supported transports (via the SDK):
- **stdio**: Spawn a subprocess (``mcp.client.stdio.stdio_client``)
- **http**: Streamable HTTP (``mcp.client.streamable_http.streamable_http_client``)

Core operations:
- ``connect()`` → open transport + initialize handshake + discover tools
- ``list_tools()`` / ``call_tool()`` → tool operations
- ``list_resources()`` / ``read_resource()`` → resource operations
- ``close()`` → clean shutdown

Requires: ``pip install mcp``
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any

from .sdk_loader import import_external_mcp_module, load_external_mcp_package
from .types import (
    ConnectionStatus,
    McpConnection,
    McpHttpConfig,
    McpResourceInfo,
    McpServerConfig,
    McpStdioConfig,
    McpToolInfo,
)

logger = logging.getLogger(__name__)


class McpClient:
    """MCP client for a single server connection, backed by the official SDK.

    Usage::

        client = McpClient("my-server", McpStdioConfig(command="npx", args=["-y", "@some/mcp-server"]))
        await client.connect()
        tools = await client.list_tools()
        result = await client.call_tool("tool_name", {"arg": "value"})
        await client.close()
    """

    def __init__(self, name: str, config: McpServerConfig) -> None:
        self._name = name
        self._config = config
        self._connection = McpConnection(name=name, config=config)
        self._session: Any = None  # mcp.ClientSession
        self._exit_stack: AsyncExitStack | None = None
        self._logging_callback: Any = None

    @property
    def connection(self) -> McpConnection:
        return self._connection

    @property
    def is_connected(self) -> bool:
        return self._connection.status == ConnectionStatus.CONNECTED

    # ── Lifecycle ───────────────────────────────────────────────────

    async def connect(self) -> McpConnection:
        """Connect to the MCP server and perform the initialize handshake."""
        try:
            self._exit_stack = AsyncExitStack()
            await self._exit_stack.__aenter__()

            if isinstance(self._config, McpStdioConfig):
                session = await self._open_stdio()
            elif isinstance(self._config, McpHttpConfig):
                session = await self._open_http()
            else:
                raise ValueError(f"Unsupported config type: {type(self._config)}")

            self._session = session

            init_result = await session.initialize()
            if hasattr(init_result, "serverInfo") and init_result.serverInfo:
                si = init_result.serverInfo
                self._connection.server_info = {
                    "name": getattr(si, "name", ""),
                    "version": getattr(si, "version", ""),
                }
            capabilities = getattr(init_result, "capabilities", None)
            if capabilities is not None:
                if hasattr(capabilities, "model_dump"):
                    self._connection.capabilities = capabilities.model_dump()
                elif isinstance(capabilities, dict):
                    self._connection.capabilities = dict(capabilities)
            if hasattr(init_result, "instructions"):
                self._connection.instructions = init_result.instructions

            self._connection.status = ConnectionStatus.CONNECTED

            tools = await self.list_tools()
            self._connection.tools = tools

            try:
                resources = await self.list_resources()
                self._connection.resources = resources
            except Exception as exc:
                logger.debug("MCP server '%s' does not support resources: %s", self._name, exc)

            logger.info(
                "MCP server '%s' connected: %d tools, %d resources",
                self._name, len(self._connection.tools), len(self._connection.resources),
            )

        except Exception as exc:
            self._connection.status = ConnectionStatus.FAILED
            self._connection.error = str(exc)
            logger.error("Failed to connect to MCP server '%s': %s", self._name, exc)
            await self._cleanup()

        return self._connection

    async def close(self) -> None:
        """Shut down the connection."""
        await self._cleanup()
        self._connection.status = ConnectionStatus.DISABLED

    async def _cleanup(self) -> None:
        self._session = None
        if self._exit_stack is not None:
            try:
                await self._exit_stack.__aexit__(None, None, None)
            except Exception:
                logger.debug("MCP cleanup error for '%s'", self._name, exc_info=True)
            self._exit_stack = None

    async def __aenter__(self) -> McpClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ── Transport setup ─────────────────────────────────────────────

    async def _open_stdio(self) -> Any:
        """Open a stdio transport and return a ClientSession."""
        mcp_sdk = load_external_mcp_package()
        stdio_sdk = import_external_mcp_module("client.stdio")

        cfg = self._config
        assert isinstance(cfg, McpStdioConfig)

        server_params = mcp_sdk.StdioServerParameters(
            command=cfg.command,
            args=cfg.args,
            env=cfg.env if cfg.env else None,
        )

        assert self._exit_stack is not None
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            stdio_sdk.stdio_client(server_params)
        )
        session = await self._exit_stack.enter_async_context(
            mcp_sdk.ClientSession(
                read_stream,
                write_stream,
                logging_callback=self._logging_callback,
            )
        )
        return session

    async def _open_http(self) -> Any:
        """Open a Streamable HTTP transport and return a ClientSession."""
        import httpx
        from .oauth import build_oauth_http_client

        mcp_sdk = load_external_mcp_package()
        http_sdk = import_external_mcp_module("client.streamable_http")
        cfg = self._config
        assert isinstance(cfg, McpHttpConfig)

        assert self._exit_stack is not None
        http_client = build_oauth_http_client(self._name, cfg.url)
        if http_client is not None:
            http_client = await self._exit_stack.enter_async_context(http_client)
        else:
            http_client = await self._exit_stack.enter_async_context(
                httpx.AsyncClient(
                    headers=cfg.headers if cfg.headers else None,
                    timeout=30.0,
                )
            )
        read_stream, write_stream, _ = await self._exit_stack.enter_async_context(
            http_sdk.streamable_http_client(cfg.url, http_client=http_client)
        )
        session = await self._exit_stack.enter_async_context(
            mcp_sdk.ClientSession(
                read_stream,
                write_stream,
                logging_callback=self._logging_callback,
            )
        )
        return session

    def set_logging_callback(self, callback: Any) -> None:
        """Install or replace the MCP logging notification callback."""
        self._logging_callback = callback
        if self._session is not None:
            try:
                self._session._logging_callback = callback
            except Exception:
                logger.debug("Failed to hot-swap MCP logging callback", exc_info=True)

    # ── MCP operations ──────────────────────────────────────────────

    async def list_tools(self) -> list[McpToolInfo]:
        """Discover available tools from the server."""
        if self._session is None:
            return []
        result = await self._session.list_tools()
        tools: list[McpToolInfo] = []
        for t in result.tools:
            tools.append(McpToolInfo(
                name=t.name,
                description=t.description or "",
                input_schema=t.inputSchema if hasattr(t, "inputSchema") else {},
                server_name=self._name,
            ))
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Invoke a tool and return the result as a dict."""
        if self._session is None:
            raise McpError("Not connected")

        result = await self._session.call_tool(name, arguments or {})

        content_list: list[dict[str, Any]] = []
        for block in result.content:
            if hasattr(block, "text"):
                content_list.append({"type": "text", "text": block.text})
            elif hasattr(block, "data"):
                content_list.append({
                    "type": "image",
                    "data": block.data,
                    "mimeType": getattr(block, "mimeType", "image/png"),
                })
            elif hasattr(block, "resource"):
                r = block.resource
                content_list.append({
                    "type": "resource",
                    "resource": {
                        "uri": str(getattr(r, "uri", "")),
                        "text": getattr(r, "text", ""),
                    },
                })
            else:
                content_list.append({"type": "unknown", "raw": str(block)})

        return {
            "content": content_list,
            "isError": getattr(result, "isError", False),
        }

    async def list_resources(self) -> list[McpResourceInfo]:
        """Discover available resources from the server."""
        if self._session is None:
            return []
        result = await self._session.list_resources()
        resources: list[McpResourceInfo] = []
        for r in result.resources:
            resources.append(McpResourceInfo(
                uri=str(r.uri),
                name=r.name,
                description=getattr(r, "description", "") or "",
                mime_type=getattr(r, "mimeType", "") or "",
                server_name=self._name,
            ))
        return resources

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Read a resource by URI."""
        if self._session is None:
            raise McpError("Not connected")
        result = await self._session.read_resource(uri)
        contents: list[dict[str, str]] = []
        for item in result.contents:
            contents.append({
                "uri": str(item.uri),
                "text": getattr(item, "text", ""),
                "mimeType": getattr(item, "mimeType", ""),
            })
        return {"contents": contents}


class McpError(Exception):
    """Error from an MCP server."""

    def __init__(self, error: dict[str, Any] | str) -> None:
        if isinstance(error, str):
            super().__init__(error)
            self.code = -1
            self.data = None
        else:
            super().__init__(error.get("message", str(error)))
            self.code = error.get("code", -1)
            self.data = error.get("data")


# ── Error hierarchy ──────────────────────────────────────────────────


class MCPClientError(McpError):
    """Base for all client-side MCP errors."""


class MCPConnectionError(MCPClientError):
    """Failed to establish or maintain a connection."""


class MCPTimeoutError(MCPClientError):
    """Operation exceeded its deadline."""


class MCPProtocolError(MCPClientError):
    """Server sent an invalid or unexpected JSON-RPC message."""


# ── Retry utility ────────────────────────────────────────────────────

_INITIAL_BACKOFF_S = 1.0
_MAX_BACKOFF_S = 30.0


async def retry_with_backoff(
    operation: Any,
    max_retries: int = 5,
    *,
    initial_backoff: float = _INITIAL_BACKOFF_S,
    max_backoff: float = _MAX_BACKOFF_S,
    retryable: tuple[type[BaseException], ...] = (MCPConnectionError, MCPTimeoutError, OSError),
) -> Any:
    """Execute *operation* with exponential backoff on transient errors.

    Mirrors the ``reconnectWithBackoff`` loop in
    ``useManageMCPConnections.ts`` (constants ``INITIAL_BACKOFF_MS``,
    ``MAX_BACKOFF_MS``, ``MAX_RECONNECT_ATTEMPTS``).

    *operation* must be an async callable (no arguments).
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return await operation()
        except retryable as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            backoff = min(initial_backoff * (2 ** (attempt - 1)), max_backoff)
            logger.debug(
                "Retry %d/%d after %.1fs: %s", attempt, max_retries, backoff, exc,
            )
            await asyncio.sleep(backoff)
    raise MCPConnectionError(f"Failed after {max_retries} retries: {last_exc}") from last_exc


# ── Connection lifecycle ─────────────────────────────────────────────

from .types import MCPConnectionState  # noqa: E402 (placed after McpError for readability)

StateChangeCallback = Any  # Callable[[MCPConnectionState, MCPConnectionState], None]


class MCPConnectionLifecycle:
    """Connection state machine with auto-reconnect.

    Wraps an :class:`McpClient` and provides:
    - ``MCPConnectionState`` tracking (DISCONNECTED → CONNECTING → CONNECTED)
    - Listener registration via ``on_state_change``
    - Automatic reconnection with exponential backoff on connection loss
    - ``health_check()`` ping that measures round-trip latency

    Modelled after the ``onclose`` reconnect handler and pending-state
    updates in ``useManageMCPConnections.ts``.
    """

    def __init__(
        self,
        client: McpClient,
        *,
        auto_reconnect: bool = True,
        max_reconnect_attempts: int = 5,
        connection_timeout: float = 30.0,
    ) -> None:
        self._client = client
        self._state = MCPConnectionState.DISCONNECTED
        self._auto_reconnect = auto_reconnect
        self._max_reconnect_attempts = max_reconnect_attempts
        self._connection_timeout = connection_timeout
        self._listeners: list[StateChangeCallback] = []
        self._reconnect_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> MCPConnectionState:
        return self._state

    @property
    def client(self) -> McpClient:
        return self._client

    def on_state_change(self, callback: StateChangeCallback) -> None:
        """Register a ``(old_state, new_state)`` listener."""
        self._listeners.append(callback)

    def _set_state(self, new_state: MCPConnectionState) -> None:
        old = self._state
        if old == new_state:
            return
        self._state = new_state
        for cb in self._listeners:
            try:
                cb(old, new_state)
            except Exception:
                logger.debug("State change callback error", exc_info=True)

    async def connect(self) -> McpConnection:
        """Connect with timeout and state tracking."""
        self._set_state(MCPConnectionState.CONNECTING)
        try:
            conn = await asyncio.wait_for(
                self._client.connect(),
                timeout=self._connection_timeout,
            )
            if self._client.is_connected:
                self._set_state(MCPConnectionState.CONNECTED)
            else:
                self._set_state(MCPConnectionState.ERROR)
            return conn
        except asyncio.TimeoutError:
            self._set_state(MCPConnectionState.ERROR)
            raise MCPTimeoutError(
                f"Connection to '{self._client._name}' timed out "
                f"after {self._connection_timeout}s"
            )
        except Exception:
            self._set_state(MCPConnectionState.ERROR)
            raise

    async def disconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            self._reconnect_task = None
        await self._client.close()
        self._set_state(MCPConnectionState.DISCONNECTED)

    async def reconnect(self) -> McpConnection:
        """Close then re-open with backoff retries."""
        self._set_state(MCPConnectionState.RECONNECTING)
        await self._client.close()

        async def _attempt() -> McpConnection:
            client = McpClient(self._client._name, self._client._config)
            conn = await asyncio.wait_for(
                client.connect(), timeout=self._connection_timeout,
            )
            if client.is_connected:
                self._client = client
                return conn
            raise MCPConnectionError(
                f"Reconnect to '{client._name}' failed: {conn.error}"
            )

        try:
            conn = await retry_with_backoff(
                _attempt, max_retries=self._max_reconnect_attempts,
            )
            self._set_state(MCPConnectionState.CONNECTED)
            return conn
        except Exception:
            self._set_state(MCPConnectionState.ERROR)
            raise

    def start_auto_reconnect(self) -> None:
        """Begin background reconnect loop (fire-and-forget)."""
        if not self._auto_reconnect:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._auto_reconnect_loop())

    async def _auto_reconnect_loop(self) -> None:
        try:
            await self.reconnect()
        except MCPClientError as exc:
            logger.warning("Auto-reconnect gave up for '%s': %s", self._client._name, exc)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Auto-reconnect unexpected error", exc_info=True)

    async def health_check(self) -> float:
        """Ping the server by listing tools; return latency in milliseconds.

        Raises ``MCPConnectionError`` if not connected or ping fails.
        """
        import time as _time

        if not self._client.is_connected:
            raise MCPConnectionError("Not connected")
        start = _time.monotonic()
        try:
            await asyncio.wait_for(
                self._client.list_tools(), timeout=10.0,
            )
        except asyncio.TimeoutError:
            raise MCPTimeoutError("Health check timed out")
        except Exception as exc:
            raise MCPConnectionError(f"Health check failed: {exc}") from exc
        elapsed_ms = (_time.monotonic() - start) * 1000
        return elapsed_ms
