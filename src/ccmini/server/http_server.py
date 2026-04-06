"""Built-in HTTP server — REST and optional WebSocket API for the agent.

Ported from Claude Code's ``server/createDirectConnectSession`` and
``directConnectManager`` patterns:

- ``AgentHTTPServer`` exposes REST routes for queries, tools, sessions, status
- ``DirectConnectManager`` handles concurrent session isolation, timeouts, cleanup
- Uses stdlib ``asyncio`` streams; optional ``aiohttp`` upgrade if available
- CORS headers on every response
- Token-based authentication
- Basic rate limiting (per-IP sliding window)
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 7780
_RATE_LIMIT_WINDOW = 60.0
_RATE_LIMIT_MAX = 60


# ── Configuration ───────────────────────────────────────────────────

@dataclass(slots=True)
class ServerConfig:
    """HTTP server configuration."""

    host: str = "127.0.0.1"
    port: int = _DEFAULT_PORT
    api_key: str = ""
    cors_origins: str = "*"
    rate_limit_max: int = _RATE_LIMIT_MAX
    rate_limit_window: float = _RATE_LIMIT_WINDOW
    session_timeout: float = 3600.0
    max_sessions: int = 8

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = secrets.token_urlsafe(32)


# ── Session management ──────────────────────────────────────────────

@dataclass
class _SessionSlot:
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    messages: list[dict[str, Any]] = field(default_factory=list)
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)
    next_sequence_num: int = 1
    event_log: list[dict[str, Any]] = field(default_factory=list)


class DirectConnectManager:
    """Manage multiple concurrent agent sessions with isolation.

    Each session has its own message history, timeout, and lifecycle.
    Mirrors Claude Code's ``DirectConnectSessionManager`` but adapted
    for a server-side multi-tenant model.
    """

    def __init__(self, config: ServerConfig | None = None) -> None:
        self._config = config or ServerConfig()
        self._sessions: dict[str, _SessionSlot] = {}
        self._cleanup_task: asyncio.Task[None] | None = None

    @property
    def active_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.status == "active")

    def create_session(self, metadata: dict[str, Any] | None = None) -> str:
        if self.active_count >= self._config.max_sessions:
            raise RuntimeError(
                f"Max sessions ({self._config.max_sessions}) reached"
            )
        sid = uuid.uuid4().hex[:16]
        self._sessions[sid] = _SessionSlot(
            session_id=sid, metadata=metadata or {},
        )
        logger.debug("Session created: %s", sid)
        return sid

    def get_session(self, session_id: str) -> _SessionSlot | None:
        return self._sessions.get(session_id)

    def touch(self, session_id: str) -> None:
        slot = self._sessions.get(session_id)
        if slot is not None:
            slot.last_activity = time.time()

    def end_session(self, session_id: str) -> bool:
        slot = self._sessions.get(session_id)
        if slot is None:
            return False
        slot.status = "ended"
        return True

    def remove_session(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {
                "session_id": s.session_id,
                "status": s.status,
                "created_at": s.created_at,
                "last_activity": s.last_activity,
                "message_count": len(s.messages),
                "next_sequence_num": s.next_sequence_num,
                "event_count": len(s.event_log),
            }
            for s in self._sessions.values()
        ]

    def cleanup_expired(self) -> int:
        """Remove sessions that have exceeded the timeout. Returns count removed."""
        now = time.time()
        expired = [
            sid
            for sid, slot in self._sessions.items()
            if slot.status == "active"
            and (now - slot.last_activity) > self._config.session_timeout
        ]
        for sid in expired:
            self._sessions[sid].status = "expired"
            logger.debug("Session expired: %s", sid)
        return len(expired)

    async def start_cleanup_loop(self, interval: float = 60.0) -> None:
        """Run periodic cleanup in the background."""
        async def _loop() -> None:
            while True:
                await asyncio.sleep(interval)
                self.cleanup_expired()

        self._cleanup_task = asyncio.create_task(_loop())

    def stop_cleanup(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None


# ── Rate limiter ────────────────────────────────────────────────────

class _RateLimiter:
    """Simple sliding-window per-IP rate limiter."""

    def __init__(self, max_requests: int, window: float) -> None:
        self._max = max_requests
        self._window = window
        self._hits: dict[str, list[float]] = {}

    def allow(self, ip: str) -> bool:
        now = time.time()
        timestamps = self._hits.get(ip, [])
        timestamps = [t for t in timestamps if now - t < self._window]
        if len(timestamps) >= self._max:
            self._hits[ip] = timestamps
            return False
        timestamps.append(now)
        self._hits[ip] = timestamps
        return True


# ── HTTP Server ─────────────────────────────────────────────────────

class AgentHTTPServer:
    """Async HTTP server exposing the agent via REST API.

    Routes
    ------
    - ``POST /api/query``        — send query, get response
    - ``POST /api/tool``         — execute a tool directly
    - ``GET  /api/status``       — agent / server status
    - ``GET  /api/sessions``     — list active sessions
    - ``POST /api/session``      — create a new session
    - ``DELETE /api/session/{id}``— end a session
    - ``GET  /api/tools``        — list available tools
    - ``GET  /api/kairos/inbox`` — Kairos inbox (file deliveries, push, PR intent)

    All routes require ``Authorization: Bearer <api_key>`` header.
    """

    def __init__(
        self,
        config: ServerConfig | None = None,
        *,
        on_query: Any = None,
        on_tool_call: Any = None,
        on_tool_results: Any = None,
        on_session_created: Any = None,
        on_session_ended: Any = None,
        tools_list: list[dict[str, Any]] | None = None,
    ) -> None:
        self._config = config or ServerConfig()
        self._manager = DirectConnectManager(self._config)
        self._limiter = _RateLimiter(
            self._config.rate_limit_max, self._config.rate_limit_window,
        )
        self._on_query = on_query
        self._on_tool_call = on_tool_call
        self._on_tool_results = on_tool_results
        self._on_session_created = on_session_created
        self._on_session_ended = on_session_ended
        self._tools_list = tools_list or []
        self._server: asyncio.Server | None = None
        self._started_at = 0.0

    @property
    def manager(self) -> DirectConnectManager:
        return self._manager

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    # ── Lifecycle ───────────────────────────────────────────────────

    async def start(self) -> None:
        if self._server is not None:
            return

        self._started_at = time.time()

        async def handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        ) -> None:
            try:
                await self._handle_request(reader, writer)
            except Exception:
                logger.debug("HTTP handler error", exc_info=True)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

        self._server = await asyncio.start_server(
            handler, self._config.host, self._config.port,
        )
        await self._manager.start_cleanup_loop()
        logger.info(
            "Agent HTTP server listening on %s:%d",
            self._config.host, self._config.port,
        )

    async def stop(self) -> None:
        self._manager.stop_cleanup()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("Agent HTTP server stopped")

    # ── Request router ──────────────────────────────────────────────

    async def _handle_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        request_line = (await reader.readline()).decode("utf-8", errors="replace").strip()
        parts = request_line.split()
        method = parts[0] if parts else "GET"
        path = parts[1] if len(parts) > 1 else "/"

        headers = await _read_headers(reader)
        body = await _read_body(reader, headers)
        remote_ip = _peer_ip(writer)

        # Rate limit
        if not self._limiter.allow(remote_ip):
            _send(writer, 429, {"error": "Rate limit exceeded"}, self._cors_headers())
            return

        # CORS preflight
        if method == "OPTIONS":
            _send(writer, 204, {}, self._cors_headers())
            return

        # Auth
        auth = headers.get("authorization", "")
        if not secrets.compare_digest(auth, f"Bearer {self._config.api_key}"):
            _send(writer, 401, {"error": "Unauthorized"}, self._cors_headers())
            return

        # Route dispatch
        cors = self._cors_headers()
        try:
            if path == "/api/status" and method == "GET":
                await self._route_status(writer, cors)
            elif path == "/api/sessions" and method == "GET":
                await self._route_list_sessions(writer, cors)
            elif path == "/api/session" and method == "POST":
                await self._route_create_session(writer, body, cors)
            elif path.startswith("/api/session/") and method == "DELETE":
                sid = path.split("/")[-1]
                await self._route_delete_session(writer, sid, cors)
            elif path == "/api/query" and method == "POST":
                await self._route_query(writer, body, cors)
            elif path == "/api/tool" and method == "POST":
                await self._route_tool(writer, body, cors)
            elif path == "/api/tool-results" and method == "POST":
                await self._route_tool_results(writer, body, cors)
            elif path.startswith("/api/events") and method == "GET":
                await self._route_events(writer, path, cors)
            elif path == "/api/tools" and method == "GET":
                await self._route_tools(writer, cors)
            elif path.startswith("/api/kairos/inbox") and method == "GET":
                await self._route_kairos_inbox(writer, path, cors)
            else:
                _send(writer, 404, {"error": "Not found"}, cors)
        except Exception as exc:
            logger.error("Route error: %s", exc, exc_info=True)
            _send(writer, 500, {"error": str(exc)}, cors)

    # ── Route handlers ──────────────────────────────────────────────

    async def _route_status(
        self, writer: asyncio.StreamWriter, cors: dict[str, str],
    ) -> None:
        _send(writer, 200, {
            "status": "running",
            "uptime": time.time() - self._started_at,
            "active_sessions": self._manager.active_count,
            "tools_count": len(self._tools_list),
        }, cors)

    async def _route_list_sessions(
        self, writer: asyncio.StreamWriter, cors: dict[str, str],
    ) -> None:
        _send(writer, 200, {"sessions": self._manager.list_sessions()}, cors)

    async def _route_create_session(
        self,
        writer: asyncio.StreamWriter,
        body: bytes,
        cors: dict[str, str],
    ) -> None:
        data = _parse_json(body)
        try:
            sid = self._manager.create_session(data.get("metadata"))
            if self._on_session_created is not None:
                created = self._on_session_created(sid, data.get("metadata") or {})
                if asyncio.iscoroutine(created):
                    await created
            self._record_event(sid, "session_created", {"metadata": data.get("metadata", {})})
            _send(writer, 200, {"session_id": sid}, cors)
        except RuntimeError as exc:
            _send(writer, 429, {"error": str(exc)}, cors)

    async def _route_delete_session(
        self,
        writer: asyncio.StreamWriter,
        session_id: str,
        cors: dict[str, str],
    ) -> None:
        slot = self._manager.get_session(session_id)
        if slot is None:
            _send(writer, 404, {"error": f"Session {session_id} not found"}, cors)
            return

        if self._manager.end_session(session_id):
            self._record_event(session_id, "session_ended", {})
            if self._on_session_ended is not None:
                ended = self._on_session_ended(session_id)
                if asyncio.iscoroutine(ended):
                    await ended
            _send(writer, 200, {"status": "ended"}, cors)
        else:
            _send(writer, 404, {"error": f"Session {session_id} not found"}, cors)

    async def _route_query(
        self,
        writer: asyncio.StreamWriter,
        body: bytes,
        cors: dict[str, str],
    ) -> None:
        data = _parse_json(body)
        session_id = data.get("session_id", "")
        text = data.get("text", "")
        if not text:
            _send(writer, 400, {"error": "Missing 'text' field"}, cors)
            return

        slot = None
        if session_id:
            slot = self._manager.get_session(session_id)
            if slot is None:
                _send(writer, 404, {"error": f"Session {session_id} not found"}, cors)
                return
            if slot.status != "active":
                _send(writer, 409, {"error": f"Session {session_id} is not active"}, cors)
                return
            self._manager.touch(session_id)
            slot.messages.append({"role": "user", "text": text, "timestamp": time.time()})
            self._record_event(session_id, "user_query", {"text": text})

        if self._on_query is not None:
            try:
                result = self._on_query(session_id, text)
                if asyncio.iscoroutine(result):
                    result = await result
                if slot is not None and isinstance(result, dict):
                    status = result.get("status", "")
                    self._record_event(session_id, "query_result", dict(result))
                    if status == "completed":
                        slot.messages.append({
                            "role": "assistant",
                            "text": str(result.get("response", "")),
                            "timestamp": time.time(),
                        })
                    elif status == "pending_tool_call":
                        slot.messages.append({
                            "role": "assistant",
                            "pending_tool_calls": result.get("calls", []),
                            "run_id": result.get("run_id", ""),
                            "timestamp": time.time(),
                        })
                elif slot is not None:
                    slot.messages.append({
                        "role": "assistant",
                        "text": str(result),
                        "timestamp": time.time(),
                    })
                    self._record_event(session_id, "query_result", {"status": "completed", "response": str(result)})
                if isinstance(result, dict):
                    _send(writer, 200, result, cors)
                else:
                    _send(writer, 200, {"status": "completed", "response": str(result)}, cors)
            except RuntimeError as exc:
                _send(writer, 409, {"error": str(exc)}, cors)
            except Exception as exc:
                _send(writer, 500, {"error": str(exc)}, cors)
        else:
            _send(writer, 501, {"error": "No query handler registered"}, cors)

    async def _route_tool(
        self,
        writer: asyncio.StreamWriter,
        body: bytes,
        cors: dict[str, str],
    ) -> None:
        data = _parse_json(body)
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input", {})
        session_id = data.get("session_id", "")
        if not tool_name:
            _send(writer, 400, {"error": "Missing 'tool_name'"}, cors)
            return

        slot = None
        if session_id:
            slot = self._manager.get_session(session_id)
            if slot is None:
                _send(writer, 404, {"error": f"Session {session_id} not found"}, cors)
                return
            if slot.status != "active":
                _send(writer, 409, {"error": f"Session {session_id} is not active"}, cors)
                return
            self._manager.touch(session_id)

        if self._on_tool_call is not None:
            try:
                result = self._on_tool_call(session_id, tool_name, tool_input)
                if asyncio.iscoroutine(result):
                    result = await result
                if slot is not None:
                    slot.messages.append({
                        "role": "tool",
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "result": str(result),
                        "timestamp": time.time(),
                    })
                if session_id:
                    self._record_event(
                        session_id,
                        "tool_call_result",
                        {"tool_name": tool_name, "tool_input": tool_input, "result": str(result)},
                    )
                _send(writer, 200, {"tool_name": tool_name, "result": str(result)}, cors)
            except Exception as exc:
                _send(writer, 500, {"error": str(exc)}, cors)
        else:
            _send(writer, 501, {"error": "No tool handler registered"}, cors)

    async def _route_tool_results(
        self,
        writer: asyncio.StreamWriter,
        body: bytes,
        cors: dict[str, str],
    ) -> None:
        data = _parse_json(body)
        session_id = data.get("session_id", "")
        run_id = data.get("run_id", "")
        results = data.get("results", [])
        if not session_id:
            _send(writer, 400, {"error": "Missing 'session_id'"}, cors)
            return
        if not run_id:
            _send(writer, 400, {"error": "Missing 'run_id'"}, cors)
            return
        if not isinstance(results, list):
            _send(writer, 400, {"error": "Missing or invalid 'results'"}, cors)
            return

        slot = self._manager.get_session(session_id)
        if slot is None:
            _send(writer, 404, {"error": f"Session {session_id} not found"}, cors)
            return
        if slot.status != "active":
            _send(writer, 409, {"error": f"Session {session_id} is not active"}, cors)
            return
        self._manager.touch(session_id)

        if self._on_tool_results is not None:
            try:
                result = self._on_tool_results(session_id, run_id, results)
                if asyncio.iscoroutine(result):
                    result = await result
                self._record_event(
                    session_id,
                    "tool_results_submitted",
                    {"run_id": run_id, "results": results},
                )
                if isinstance(result, dict):
                    self._record_event(session_id, "tool_results_response", dict(result))
                    if result.get("status") == "completed":
                        slot.messages.append({
                            "role": "assistant",
                            "text": str(result.get("response", "")),
                            "timestamp": time.time(),
                        })
                    _send(writer, 200, result, cors)
                else:
                    _send(writer, 200, {"status": "completed", "response": str(result)}, cors)
            except RuntimeError as exc:
                _send(writer, 409, {"error": str(exc)}, cors)
            except Exception as exc:
                _send(writer, 500, {"error": str(exc)}, cors)
        else:
            _send(writer, 501, {"error": "No tool-results handler registered"}, cors)

    async def _route_events(
        self,
        writer: asyncio.StreamWriter,
        path: str,
        cors: dict[str, str],
    ) -> None:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(path)
        query = parse_qs(parsed.query)
        session_id = str(query.get("session_id", [""])[0]).strip()
        if not session_id:
            _send(writer, 400, {"error": "Missing session_id"}, cors)
            return
        slot = self._manager.get_session(session_id)
        if slot is None:
            _send(writer, 404, {"error": f"Session {session_id} not found"}, cors)
            return
        since = int(query.get("since", ["0"])[0] or 0)
        limit = int(query.get("limit", ["100"])[0] or 100)
        events = [
            item for item in slot.event_log
            if int(item.get("sequence_num", 0)) > since
        ]
        if limit > 0:
            events = events[:limit]
        _send(
            writer,
            200,
            {
                "session_id": session_id,
                "events": events,
                "next_sequence_num": slot.next_sequence_num,
            },
            cors,
        )

    async def _route_tools(
        self, writer: asyncio.StreamWriter, cors: dict[str, str],
    ) -> None:
        _send(writer, 200, {"tools": self._tools_list}, cors)

    async def _route_kairos_inbox(
        self,
        writer: asyncio.StreamWriter,
        path: str,
        cors: dict[str, str],
    ) -> None:
        from urllib.parse import parse_qs, urlparse

        from ..kairos.inbox import get_inbox_snapshot

        parsed = urlparse(path)
        query = parse_qs(parsed.query)
        limit_raw = (query.get("limit") or ["50"])[0]
        try:
            limit = max(1, min(500, int(limit_raw)))
        except ValueError:
            limit = 50
        stream_arg = (query.get("stream") or ["all"])[0].strip().lower()
        valid = frozenset({"file_deliveries", "push_notifications", "subscribe_pr"})
        if stream_arg in ("all", "", "*"):
            streams = None
        elif stream_arg in valid:
            streams = frozenset({stream_arg})
        else:
            _send(
                writer,
                400,
                {
                    "error": "Invalid stream",
                    "valid": sorted(valid) + ["all"],
                },
                cors,
            )
            return
        data = get_inbox_snapshot(limit_per_stream=limit, streams=streams)
        _send(writer, 200, {"inbox": data, "limit": limit, "stream": stream_arg}, cors)

    # ── Helpers ─────────────────────────────────────────────────────

    def _cors_headers(self) -> dict[str, str]:
        return {
            "Access-Control-Allow-Origin": self._config.cors_origins,
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        }

    def _record_event(self, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
        slot = self._manager.get_session(session_id)
        if slot is None:
            return
        slot.event_log.append({
            "sequence_num": slot.next_sequence_num,
            "type": event_type,
            "payload": payload,
            "timestamp": time.time(),
        })
        slot.next_sequence_num += 1
        if len(slot.event_log) > 1000:
            slot.event_log = slot.event_log[-1000:]


# ── Shared helpers ──────────────────────────────────────────────────

async def _read_headers(reader: asyncio.StreamReader) -> dict[str, str]:
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        decoded = line.decode("utf-8", errors="replace").strip()
        if ":" in decoded:
            k, v = decoded.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return headers


async def _read_body(
    reader: asyncio.StreamReader, headers: dict[str, str],
) -> bytes:
    length = int(headers.get("content-length", "0"))
    if length > 0:
        return await reader.readexactly(length)
    return b""


def _parse_json(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {}


def _peer_ip(writer: asyncio.StreamWriter) -> str:
    try:
        peername = writer.get_extra_info("peername")
        if peername:
            return str(peername[0])
    except Exception:
        pass
    return "unknown"


def _send(
    writer: asyncio.StreamWriter,
    status: int,
    body: dict[str, Any],
    extra_headers: dict[str, str] | None = None,
) -> None:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    status_map = {
        200: "OK", 204: "No Content", 400: "Bad Request",
        401: "Unauthorized", 404: "Not Found", 429: "Too Many Requests",
        500: "Internal Server Error", 501: "Not Implemented",
        409: "Conflict",
    }
    status_text = status_map.get(status, "Error")
    header_lines = [
        f"HTTP/1.1 {status} {status_text}",
        "Content-Type: application/json",
        f"Content-Length: {len(payload)}",
        "Connection: close",
    ]
    if extra_headers:
        for k, v in extra_headers.items():
            header_lines.append(f"{k}: {v}")
    header_lines.append("")
    header_lines.append("")
    writer.write("\r\n".join(header_lines).encode("utf-8") + payload)
