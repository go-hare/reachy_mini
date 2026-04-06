"""Bridge server — unified HTTP / SSE / WebSocket bridge for remote control."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .api import BridgeAPI
from .messaging import (
    BridgeMessage,
    MessageType,
    decode,
    encode,
    make_error,
    make_heartbeat,
)

logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────

@dataclass(slots=True)
class BridgeConfig:
    """Configuration for the bridge server."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 7779
    auth_token: str = ""
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
    heartbeat_interval: float = 30.0
    connection_timeout: float = 300.0
    max_connections: int = 10
    # HTTP fallback is the default because several local workflows
    # depend on /bridge/message and /bridge/events existing.
    prefer_websocket: bool = False

    def __post_init__(self) -> None:
        if not self.auth_token:
            self.auth_token = secrets.token_urlsafe(32)

    @property
    def ssl(self) -> bool:
        return bool(self.ssl_certfile and self.ssl_keyfile)


# ── Connection tracking ─────────────────────────────────────────────

@dataclass
class _Connection:
    """Internal bookkeeping for a single WebSocket connection."""

    conn_id: str
    session_id: str
    remote: str
    connected_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    websocket: Any = None


# ── Bridge server ───────────────────────────────────────────────────

class BridgeServer:
    """Unified bridge host for HTTP, SSE, and WebSocket clients."""

    def __init__(
        self,
        config: BridgeConfig,
        api: BridgeAPI | None = None,
    ) -> None:
        self._config = config
        self._api = api or BridgeAPI()
        self._connections: dict[str, _Connection] = {}
        self._server: Any = None
        self._runner: Any = None
        self._site: Any = None
        self._running = False
        self._heartbeat_task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def config(self) -> BridgeConfig:
        return self._config

    @property
    def api(self) -> BridgeAPI:
        return self._api

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    # ── Lifecycle ───────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the bridge server."""
        if self._running:
            return

        await self._start_unified_server()
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(
            "Bridge server started on %s:%d (http+sse+ws)",
            self._config.host,
            self._config.port,
        )

    async def stop(self) -> None:
        """Stop the bridge server and disconnect all clients."""
        if not self._running:
            return

        self._running = False

        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        for conn in list(self._connections.values()):
            await self._close_connection(conn)
        self._connections.clear()

        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self._server = None

        logger.info("Bridge server stopped")

    # ── Unified aiohttp server ─────────────────────────────────────

    async def _start_unified_server(self) -> None:
        from aiohttp import web

        ssl_ctx = None
        if self._config.ssl:
            import ssl
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(
                self._config.ssl_certfile, self._config.ssl_keyfile,
            )

        app = web.Application()
        app.router.add_get("/", self._aiohttp_ws_handler)
        app.router.add_post("/bridge/sessions", self._aiohttp_handle_create_session)
        app.router.add_post("/bridge/message", self._aiohttp_handle_message)
        app.router.add_get("/bridge/events", self._aiohttp_handle_events)
        app.router.add_get("/bridge/events/stream", self._aiohttp_handle_event_stream)
        app.router.add_get("/bridge/status", self._aiohttp_handle_status)
        app.router.add_get("/api/kairos/inbox", self._aiohttp_kairos_inbox)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            self._config.host,
            self._config.port,
            ssl_context=ssl_ctx,
        )
        await self._site.start()
        self._server = self._runner

    async def _aiohttp_ws_handler(self, request: Any) -> Any:
        from aiohttp import web, WSMsgType

        websocket = web.WebSocketResponse(heartbeat=self._config.heartbeat_interval)
        await websocket.prepare(request)
        remote = str(getattr(request, "remote", "unknown"))

        requested_session_id = await self._authenticate_ws(websocket)
        if requested_session_id is False:
            logger.warning("Bridge auth failed from %s", remote)
            await websocket.close()
            return websocket

        if len(self._connections) >= self._config.max_connections:
            await self._send_ws_payload(
                websocket,
                encode(make_error("", "Max connections reached")),
            )
            await websocket.close()
            return websocket

        conn_id = secrets.token_hex(8)
        session_id = (
            str(requested_session_id).strip()
            if requested_session_id not in (None, False)
            else ""
        )
        if not session_id or self._api.get_session_status(session_id).get("error"):
            session_id = self._api.create_session({"remote": remote})
        conn = _Connection(
            conn_id=conn_id,
            session_id=session_id,
            remote=remote,
            websocket=websocket,
        )
        self._connections[conn_id] = conn
        logger.debug("Bridge connection %s from %s (session %s)", conn_id, remote, session_id)

        try:
            async for message in websocket:
                if message.type is WSMsgType.TEXT:
                    await self._handle_ws_message(conn, message.data)
                elif message.type is WSMsgType.BINARY:
                    await self._handle_ws_message(conn, message.data)
                elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                    break
        except Exception:
            logger.debug("Bridge connection %s closed", conn_id, exc_info=True)
        finally:
            self._connections.pop(conn_id, None)
            self._api.end_session(session_id)

        return websocket

    async def _authenticate_ws(self, websocket: Any) -> str | bool | None:
        """Wait for a single auth message and verify the token."""
        try:
            raw = await asyncio.wait_for(websocket.receive(), timeout=10.0)
            data = json.loads(getattr(raw, "data", raw))
            token = data.get("auth_token", "")
            requested_session_id = str(data.get("session_id", "")).strip() or None
            if secrets.compare_digest(token, self._config.auth_token):
                await self._send_ws_payload(
                    websocket,
                    json.dumps(
                        {
                            "status": "authenticated",
                            "session_id": requested_session_id or "",
                        }
                    ),
                )
                return requested_session_id
            await self._send_ws_payload(
                websocket,
                json.dumps({"status": "auth_failed"}),
            )
            return False
        except Exception:
            return False

    async def _handle_ws_message(self, conn: _Connection, raw: str | bytes) -> None:
        """Parse, dispatch, and respond to a WebSocket frame."""
        conn.last_heartbeat = time.time()
        try:
            msg = decode(raw)
            msg.session_id = msg.session_id or conn.session_id
            response = await self._api.handle_message(conn.session_id, msg)
            await self._send_ws_payload(conn.websocket, encode(response))
        except Exception as exc:
            err = make_error(conn.session_id, str(exc))
            try:
                await self._send_ws_payload(conn.websocket, encode(err))
            except Exception:
                pass

    async def _send_ws_payload(self, websocket: Any, payload: str) -> None:
        if hasattr(websocket, "send_str"):
            await websocket.send_str(payload)
            return
        result = websocket.send(payload)
        if asyncio.iscoroutine(result):
            await result

    async def push_event(self, session_id: str, event: dict[str, Any]) -> None:
        """Push a single bridge event to all live WebSocket clients for a session."""
        payload = encode(
            BridgeMessage(
                type=MessageType.EVENTS,
                payload={"events": [event]},
                session_id=session_id,
            )
        )
        for conn in list(self._connections.values()):
            if conn.session_id != session_id or conn.websocket is None:
                continue
            try:
                await self._send_ws_payload(conn.websocket, payload)
            except Exception:
                logger.debug("Failed to push websocket event", exc_info=True)

    async def _close_connection(self, conn: _Connection) -> None:
        if conn.websocket is not None:
            try:
                result = conn.websocket.close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

    # ── HTTP / SSE handlers ─────────────────────────────────────────

    def _is_http_authorized(self, request: Any) -> bool:
        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {self._config.auth_token}"
        return secrets.compare_digest(auth, expected)

    def _http_base_urls(self, request: Any) -> tuple[str, str]:
        scheme = "https" if self._config.ssl else "http"
        ws_scheme = "wss" if self._config.ssl else "ws"
        host = request.headers.get("Host", f"{self._config.host}:{self._config.port}")
        return (
            f"{scheme}://{host}",
            f"{ws_scheme}://{host}",
        )

    async def _aiohttp_handle_create_session(self, request: Any) -> Any:
        from aiohttp import web

        if not self._is_http_authorized(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        metadata: dict[str, Any] | None = None
        try:
            raw = await request.read()
            if raw:
                parsed = json.loads(raw.decode("utf-8"))
                if parsed is None:
                    metadata = None
                elif isinstance(parsed, dict):
                    metadata = parsed
                else:
                    return web.json_response(
                        {"error": "Session metadata must be a JSON object"},
                        status=400,
                    )
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

        session_id = self._api.create_session(metadata)
        base_url, websocket_url = self._http_base_urls(request)
        return web.json_response(
            {
                "session_id": session_id,
                "base_url": base_url,
                "websocket_url": websocket_url,
            }
        )

    async def _aiohttp_handle_message(self, request: Any) -> Any:
        from aiohttp import web
        if not self._is_http_authorized(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        body = await request.read()
        try:
            msg = decode(body)
            sid = msg.session_id or self._api.create_session()
            response = await self._api.handle_message(sid, msg)
            return web.json_response(json.loads(encode(response)))
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    async def _aiohttp_kairos_inbox(self, request: Any) -> Any:
        """``GET /api/kairos/inbox`` — same auth as other bridge HTTP routes."""
        from aiohttp import web

        from ..kairos.inbox import get_inbox_snapshot

        if not self._is_http_authorized(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            limit_raw = request.query.get("limit", "50")
            limit = max(1, min(500, int(limit_raw)))
        except ValueError:
            limit = 50
        stream_arg = str(request.query.get("stream", "all")).strip().lower()
        valid = frozenset({"file_deliveries", "push_notifications", "subscribe_pr"})
        if stream_arg in ("all", "", "*"):
            streams = None
        elif stream_arg in valid:
            streams = frozenset({stream_arg})
        else:
            return web.json_response(
                {"error": "Invalid stream", "valid": sorted(valid) + ["all"]},
                status=400,
            )
        data = get_inbox_snapshot(limit_per_stream=limit, streams=streams)
        return web.json_response({"inbox": data, "limit": limit, "stream": stream_arg})

    async def _aiohttp_handle_events(self, request: Any) -> Any:
        from aiohttp import web
        if not self._is_http_authorized(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            session_id = str(request.query.get("session_id", "")).strip()
            since = int(request.query.get("since", "0") or 0)
            limit = int(request.query.get("limit", "100") or 100)
            if not session_id:
                return web.json_response({"error": "Missing session_id"}, status=400)
            response = self._api.handle_events(session_id, since=since, limit=limit)
            return web.json_response(json.loads(encode(response)))
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    async def _aiohttp_handle_event_stream(self, request: Any) -> Any:
        from aiohttp import web

        if not self._is_http_authorized(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        session_id = str(request.query.get("session_id", "")).strip()
        last_sequence = int(request.query.get("since", "0") or 0)
        limit = int(request.query.get("limit", "100") or 100)
        if not session_id:
            return web.json_response({"error": "Missing session_id"}, status=400)
        if self._api.get_session_status(session_id).get("error"):
            return web.json_response(
                {"error": f"Unknown session: {session_id}"},
                status=404,
            )

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)

        poll_interval = min(1.0, max(0.1, self._config.heartbeat_interval / 10.0))
        last_keepalive_at = 0.0
        try:
            while self._running:
                events = self._api.get_events(session_id, since=last_sequence, limit=limit) or []
                if events:
                    for event in events:
                        seq = int(event.get("sequence_num", 0) or 0)
                        payload = json.dumps(event, ensure_ascii=False)
                        frame = (
                            "event: client_event\r\n"
                            f"id: {seq}\r\n"
                            f"data: {payload}\r\n\r\n"
                        )
                        await response.write(frame.encode("utf-8"))
                        last_sequence = max(last_sequence, seq)
                    last_keepalive_at = time.time()
                elif (time.time() - last_keepalive_at) >= self._config.heartbeat_interval:
                    await response.write(b":keepalive\n\n")
                    last_keepalive_at = time.time()
                await asyncio.sleep(poll_interval)
        except Exception:
            logger.debug("Bridge SSE stream closed", exc_info=True)
        return response

    async def _aiohttp_handle_status(self, request: Any) -> Any:
        from aiohttp import web
        if not self._is_http_authorized(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        return web.json_response(
            {
                "running": self._running,
                "connections": len(self._connections),
                "sessions": self._api.list_sessions(),
            }
        )

    # ── Heartbeat loop ──────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """Periodically send heartbeats and cull stale connections."""
        while self._running:
            await asyncio.sleep(self._config.heartbeat_interval)
            now = time.time()
            stale: list[str] = []

            for conn_id, conn in list(self._connections.items()):
                age = now - conn.last_heartbeat
                if age > self._config.connection_timeout:
                    stale.append(conn_id)
                    continue
                if conn.websocket is not None:
                    try:
                        await self._send_ws_payload(
                            conn.websocket,
                            encode(make_heartbeat()),
                        )
                    except Exception:
                        stale.append(conn_id)

            for conn_id in stale:
                conn = self._connections.pop(conn_id, None)
                if conn is not None:
                    logger.debug("Culling stale bridge connection %s", conn_id)
                    self._api.end_session(conn.session_id)
                    await self._close_connection(conn)
