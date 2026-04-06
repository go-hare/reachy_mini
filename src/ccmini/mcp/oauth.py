"""OAuth support for HTTP/SSE MCP servers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from ..paths import mini_agent_path
from .sdk_loader import import_external_mcp_module

_mcp_auth = import_external_mcp_module("client.auth")
_mcp_shared_auth = import_external_mcp_module("shared.auth")

OAuthClientProvider = _mcp_auth.OAuthClientProvider
OAuthClientInformationFull = _mcp_shared_auth.OAuthClientInformationFull
OAuthClientMetadata = _mcp_shared_auth.OAuthClientMetadata
OAuthToken = _mcp_shared_auth.OAuthToken

logger = logging.getLogger(__name__)


def _oauth_root() -> Path:
    return mini_agent_path("mcp_oauth")


def _oauth_state_path(server_name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in server_name)
    return _oauth_root() / f"{safe}.json"


class FileTokenStorage:
    """File-backed token storage for MCP OAuth flows."""

    def __init__(self, server_name: str) -> None:
        self._path = _oauth_state_path(server_name)

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._load().get("tokens")
        if not isinstance(raw, dict):
            return None
        return OAuthToken.model_validate(raw)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._load()
        data["tokens"] = tokens.model_dump(mode="json")
        data["updated_at"] = time.time()
        self._save(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._load().get("client_info")
        if not isinstance(raw, dict):
            return None
        return OAuthClientInformationFull.model_validate(raw)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._load()
        data["client_info"] = client_info.model_dump(mode="json")
        data["updated_at"] = time.time()
        self._save(data)


def has_oauth_state(server_name: str) -> bool:
    return _oauth_state_path(server_name).exists()


def clear_oauth_state(server_name: str) -> bool:
    path = _oauth_state_path(server_name)
    if not path.exists():
        return False
    path.unlink()
    return True


def get_oauth_state(server_name: str) -> dict[str, Any]:
    path = _oauth_state_path(server_name)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@dataclass
class OAuthFlowState:
    flow_id: str
    server_name: str
    server_url: str
    status: str = "starting"
    auth_url: str = ""
    redirect_uri: str = ""
    error: str = ""
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    task: asyncio.Task[None] | None = None


_active_flows: dict[str, OAuthFlowState] = {}


def get_flow_state(flow_id: str) -> OAuthFlowState | None:
    return _active_flows.get(flow_id)


async def start_oauth_flow(
    *,
    server_name: str,
    server_url: str,
    open_browser: bool = False,
    timeout: float = 300.0,
) -> OAuthFlowState:
    """Start an MCP OAuth flow and return once the auth URL is available."""
    flow = OAuthFlowState(
        flow_id=uuid.uuid4().hex[:12],
        server_name=server_name,
        server_url=server_url,
    )
    _active_flows[flow.flow_id] = flow

    auth_ready = asyncio.Event()
    callback_future: asyncio.Future[tuple[str, str | None]] = asyncio.get_running_loop().create_future()

    async def _serve_callback() -> tuple[asyncio.AbstractServer, str]:
        async def _handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                request_line = (await reader.readline()).decode("utf-8", errors="replace").strip()
                parts = request_line.split()
                path = parts[1] if len(parts) > 1 else "/"
                parsed = urlparse(path)
                params = parse_qs(parsed.query)
                code = str(params.get("code", [""])[0])
                state = params.get("state", [None])[0]
                if not callback_future.done():
                    callback_future.set_result((code, state))
                body = b"OAuth complete. You can return to mini-agent."
                header = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/plain\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "Connection: close\r\n\r\n"
                )
                writer.write(header.encode("utf-8") + body)
                await writer.drain()
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()

        server = await asyncio.start_server(_handler, "127.0.0.1", 0)
        sock = server.sockets[0]
        host, port = sock.getsockname()[:2]
        return server, f"http://{host}:{port}/callback"

    async def _runner() -> None:
        server: asyncio.AbstractServer | None = None
        try:
            server, redirect_uri = await _serve_callback()
            flow.redirect_uri = redirect_uri
            storage = FileTokenStorage(server_name)
            metadata = OAuthClientMetadata(
                redirect_uris=[redirect_uri],
                client_name="mini-agent MCP",
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
            )

            async def _redirect_handler(url: str) -> None:
                flow.auth_url = url
                flow.status = "awaiting_user"
                auth_ready.set()
                if open_browser:
                    webbrowser.open(url)

            async def _callback_handler() -> tuple[str, str | None]:
                return await asyncio.wait_for(callback_future, timeout=timeout)

            auth = OAuthClientProvider(
                server_url=server_url,
                client_metadata=metadata,
                storage=storage,
                redirect_handler=_redirect_handler,
                callback_handler=_callback_handler,
                timeout=timeout,
            )

            async with httpx.AsyncClient(auth=auth, follow_redirects=False, timeout=30.0) as client:
                response = await client.get(
                    server_url,
                    headers={"Accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"},
                )
                if response.status_code >= 400 and response.status_code not in {401, 403, 405}:
                    raise RuntimeError(f"OAuth probe failed with HTTP {response.status_code}")

            flow.status = "completed"
            flow.completed_at = time.time()
            auth_ready.set()
        except Exception as exc:
            flow.status = "error"
            flow.error = str(exc)
            flow.completed_at = time.time()
            auth_ready.set()
        finally:
            if server is not None:
                server.close()
                with contextlib.suppress(Exception):
                    await server.wait_closed()

    flow.task = asyncio.create_task(_runner())
    await asyncio.wait_for(auth_ready.wait(), timeout=15.0)
    return flow


def build_oauth_http_client(server_name: str, server_url: str) -> httpx.AsyncClient | None:
    """Create an httpx client with OAuth auth if stored state exists."""
    if not has_oauth_state(server_name):
        return None
    storage = FileTokenStorage(server_name)
    metadata = OAuthClientMetadata(
        redirect_uris=["http://127.0.0.1/unused"],
        client_name="mini-agent MCP",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )
    auth = OAuthClientProvider(
        server_url=server_url,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=None,
        callback_handler=None,
    )
    return httpx.AsyncClient(auth=auth, timeout=30.0)
