"""Bridge server — unified HTTP / SSE / WebSocket bridge for remote control."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import mini_agent_path
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


def _compat_root() -> Path:
    root = mini_agent_path("frontend_compat")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _compat_projects_path() -> Path:
    return _compat_root() / "projects.json"


def _compat_uploads_dir() -> Path:
    path = _compat_root() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _compat_uploads_meta_path() -> Path:
    return _compat_root() / "uploads.json"


def _compat_is_artifact_candidate(filename: str, mime_type: str) -> bool:
    ext = Path(filename).suffix.lower()
    if ext in {".html", ".htm", ".jsx", ".tsx", ".js", ".ts", ".md"}:
        return True
    return mime_type.startswith("text/")


def _compat_read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _compat_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _compat_skills_path() -> Path:
    return _compat_root() / "skills.json"


def _compat_skill_overrides_path() -> Path:
    return _compat_root() / "skills_overrides.json"


def _compat_read_skill_overrides() -> dict[str, Any]:
    data = _compat_read_json(_compat_skill_overrides_path(), {})
    return data if isinstance(data, dict) else {}


def _compat_write_skill_overrides(data: dict[str, Any]) -> None:
    _compat_write_json(_compat_skill_overrides_path(), data)


def _compat_skill_roots() -> list[Path]:
    roots = [
        Path.home() / ".codex" / "skills",
        Path(os.getcwd()) / ".codex" / "skills",
        Path(os.getcwd()) / ".mini_agent" / "skills",
    ]
    resolved: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        with contextlib.suppress(Exception):
            candidate = root.resolve()
            key = str(candidate)
            if candidate.is_dir() and key not in seen:
                seen.add(key)
                resolved.append(candidate)
    return resolved


def _compat_skill_timestamp(path: Path) -> str:
    with contextlib.suppress(Exception):
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(path.stat().st_mtime))
    return ""


def _compat_skill_file_tree(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        if child.name in {"__pycache__", ".DS_Store"}:
            continue
        if child.is_dir():
            entries.append(
                {
                    "name": child.name,
                    "type": "folder",
                    "children": _compat_skill_file_tree(child),
                }
            )
        else:
            entries.append({"name": child.name, "type": "file"})
    return entries


def _compat_disk_skill_id(skill_file: Path) -> str:
    digest = hashlib.sha1(str(skill_file.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"disk-skill-{digest}"


def _compat_load_disk_skills() -> list[dict[str, Any]]:
    from ..skills import parse_skill_frontmatter

    overrides = _compat_read_skill_overrides()
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in _compat_skill_roots():
        for skill_file in sorted(root.rglob("SKILL.md")):
            with contextlib.suppress(Exception):
                resolved = skill_file.resolve()
                key = str(resolved)
                if key in seen:
                    continue
                seen.add(key)
                text = resolved.read_text(encoding="utf-8")
                frontmatter, body = parse_skill_frontmatter(text)
                skill_dir = resolved.parent
                skill_id = _compat_disk_skill_id(resolved)
                title = frontmatter.description
                if not title:
                    match = re.search(r"^#\s+(.+)$", body or text, re.MULTILINE)
                    title = match.group(1).strip() if match else skill_dir.name
                relative_parent = ""
                with contextlib.suppress(Exception):
                    relative_parent = str(skill_dir.relative_to(root.parent)).replace("\\", "/")
                skills.append(
                    {
                        "id": skill_id,
                        "name": skill_dir.name,
                        "description": frontmatter.description or title or "",
                        "content": text,
                        "enabled": bool(overrides.get(skill_id, {}).get("enabled", True)),
                        "created_at": _compat_skill_timestamp(resolved),
                        "files": _compat_skill_file_tree(skill_dir),
                        "source_dir": relative_parent or skill_dir.name,
                        "is_example": False,
                        "_source": "disk",
                        "_skill_file": str(resolved),
                        "_skill_root": str(skill_dir),
                    }
                )
    skills.sort(key=lambda item: str(item.get("name", "")).lower())
    return skills


def _compat_load_custom_skills() -> list[dict[str, Any]]:
    skills = _compat_read_json(_compat_skills_path(), [])
    if not isinstance(skills, list):
        return []
    normalized: list[dict[str, Any]] = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        item = dict(skill)
        item.setdefault("enabled", True)
        item.setdefault("files", [{"name": "SKILL.md", "type": "file"}])
        item["_source"] = "custom"
        normalized.append(item)
    return normalized


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
        compat_handler: Any = None,
    ) -> None:
        self._config = config
        self._api = api or BridgeAPI()
        self._compat_handler = compat_handler
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
        app.router.add_get("/api/tasks", self._aiohttp_tasks)
        app.router.add_post("/api/tasks/control", self._aiohttp_task_control)
        app.router.add_get("/api/tasks/transcript", self._aiohttp_task_transcript)
        if self._compat_handler is not None:
            app.router.add_get("/api/system-status", self._aiohttp_compat_system_status)
            app.router.add_get("/api/user/profile", self._aiohttp_compat_user_profile)
            app.router.add_patch("/api/user/profile", self._aiohttp_compat_update_user_profile)
            app.router.add_get("/api/user/usage", self._aiohttp_compat_user_usage)
            app.router.add_get("/api/user/announcements", self._aiohttp_compat_user_announcements)
            app.router.add_post("/api/user/announcements/{announcement_id}/read", self._aiohttp_compat_mark_announcement_read)
            app.router.add_get("/api/user/models", self._aiohttp_compat_user_models)
            app.router.add_get("/api/user/sessions", self._aiohttp_compat_user_sessions)
            app.router.add_delete("/api/user/sessions/{session_id}", self._aiohttp_compat_user_delete_session)
            app.router.add_post("/api/user/sessions/logout-others", self._aiohttp_compat_user_logout_others)
            app.router.add_post("/api/user/change-password", self._aiohttp_compat_user_change_password)
            app.router.add_post("/api/user/delete-account", self._aiohttp_compat_user_delete_account)
            app.router.add_get("/api/providers", self._aiohttp_compat_providers)
            app.router.add_post("/api/providers", self._aiohttp_compat_providers_mutation)
            app.router.add_patch("/api/providers/{provider_id}", self._aiohttp_compat_providers_mutation)
            app.router.add_delete("/api/providers/{provider_id}", self._aiohttp_compat_delete_provider)
            app.router.add_get("/api/providers/models", self._aiohttp_compat_provider_models)
            app.router.add_post("/api/providers/{provider_id}/test-websearch", self._aiohttp_compat_test_provider_websearch)
            app.router.add_get("/api/projects", self._aiohttp_compat_projects)
            app.router.add_post("/api/projects", self._aiohttp_compat_projects)
            app.router.add_get("/api/projects/{project_id}", self._aiohttp_compat_project_detail)
            app.router.add_patch("/api/projects/{project_id}", self._aiohttp_compat_project_detail)
            app.router.add_delete("/api/projects/{project_id}", self._aiohttp_compat_project_detail)
            app.router.add_post("/api/projects/{project_id}/files", self._aiohttp_compat_project_upload_file)
            app.router.add_delete("/api/projects/{project_id}/files/{file_id}", self._aiohttp_compat_project_delete_file)
            app.router.add_get("/api/projects/{project_id}/conversations", self._aiohttp_compat_project_conversations)
            app.router.add_post("/api/projects/{project_id}/conversations", self._aiohttp_compat_project_create_conversation)
            app.router.add_get("/api/skills", self._aiohttp_compat_skills)
            app.router.add_post("/api/skills", self._aiohttp_compat_create_skill)
            app.router.add_get("/api/skills/{skill_id}", self._aiohttp_compat_skill_detail)
            app.router.add_get("/api/skills/{skill_id}/file", self._aiohttp_compat_skill_file)
            app.router.add_patch("/api/skills/{skill_id}", self._aiohttp_compat_update_skill)
            app.router.add_delete("/api/skills/{skill_id}", self._aiohttp_compat_delete_skill)
            app.router.add_patch("/api/skills/{skill_id}/toggle", self._aiohttp_compat_toggle_skill)
            app.router.add_get("/api/github/status", self._aiohttp_compat_github_status)
            app.router.add_get("/api/github/auth-url", self._aiohttp_compat_github_auth_url)
            app.router.add_post("/api/github/disconnect", self._aiohttp_compat_github_disconnect)
            app.router.add_get("/api/github/repos", self._aiohttp_compat_github_repos)
            app.router.add_get("/api/github/repos/{owner}/{repo}/tree", self._aiohttp_compat_github_tree)
            app.router.add_get("/api/github/repos/{owner}/{repo}/contents", self._aiohttp_compat_github_contents)
            app.router.add_post("/api/github/materialize", self._aiohttp_compat_github_materialize)
            app.router.add_get("/api/conversations", self._aiohttp_compat_list_conversations)
            app.router.add_post("/api/conversations", self._aiohttp_compat_create_conversation)
            app.router.add_get("/api/conversations/{session_id}", self._aiohttp_compat_get_conversation)
            app.router.add_patch("/api/conversations/{session_id}", self._aiohttp_compat_update_conversation)
            app.router.add_delete("/api/conversations/{session_id}", self._aiohttp_compat_delete_conversation)
            app.router.add_get("/api/conversations/{session_id}/generation-status", self._aiohttp_compat_generation_status)
            app.router.add_post("/api/conversations/{session_id}/stop-generation", self._aiohttp_compat_stop_generation)
            app.router.add_get("/api/conversations/{session_id}/context-size", self._aiohttp_compat_context_size)
            app.router.add_get("/api/conversations/{session_id}/stream-status", self._aiohttp_compat_stream_status)
            app.router.add_get("/api/conversations/{session_id}/reconnect", self._aiohttp_compat_reconnect)
            app.router.add_post("/api/conversations/{session_id}/answer", self._aiohttp_compat_answer)
            app.router.add_post("/api/conversations/{session_id}/warm", self._aiohttp_compat_warm)
            app.router.add_post("/api/conversations/{session_id}/compact", self._aiohttp_compat_compact)
            app.router.add_delete("/api/conversations/{session_id}/messages/{message_id}", self._aiohttp_compat_delete_messages_from)
            app.router.add_delete("/api/conversations/{session_id}/messages-tail/{count}", self._aiohttp_compat_delete_messages_tail)
            app.router.add_post("/api/chat", self._aiohttp_compat_chat)
            app.router.add_post("/api/upload", self._aiohttp_compat_upload)
            app.router.add_delete("/api/uploads/{file_id}", self._aiohttp_compat_delete_upload)
            app.router.add_get("/api/uploads/{file_id}/path", self._aiohttp_compat_upload_path)
            app.router.add_get("/api/uploads/{file_id}/raw", self._aiohttp_compat_upload_raw)
            app.router.add_get("/api/documents/{document_id}/raw", self._aiohttp_compat_document_raw)
            app.router.add_get("/api/artifacts", self._aiohttp_compat_artifacts)
            app.router.add_get("/api/artifacts/content", self._aiohttp_compat_artifact_content)
            app.router.add_get("/api/code/sso", self._aiohttp_compat_code_sso)
            app.router.add_get("/api/code/quota", self._aiohttp_compat_code_quota)
            app.router.add_get("/api/code/plans", self._aiohttp_compat_code_plans)

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

    async def _aiohttp_tasks(self, request: Any) -> Any:
        """``GET /api/tasks`` — expose the current task board for remote UIs."""
        from aiohttp import web

        from ..delegation.team_files import read_team_file
        from ..tools.task_tools import TaskBoard

        if not self._is_http_authorized(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        session_id = str(request.query.get("session_id", "") or "").strip()
        task_list_id = str(request.query.get("task_list_id", "") or "").strip()
        include_completed_arg = str(request.query.get("include_completed", "true") or "").strip().lower()
        include_completed = include_completed_arg not in {"0", "false", "no"}

        runtime_snapshot = self._api.get_runtime_snapshot(session_id) if session_id else None
        if not isinstance(runtime_snapshot, dict):
            runtime_snapshot = {}
        resolved_task_list_id = (
            task_list_id
            or str(runtime_snapshot.get("taskListId", "") or "").strip()
            or session_id
        )

        board = TaskBoard()
        if resolved_task_list_id:
            board.set_scope(resolved_task_list_id)

        team_name = resolved_task_list_id
        owner_activity: dict[str, bool] = {}
        if team_name:
            team_data = read_team_file(team_name)
            members = team_data.get("members", []) if isinstance(team_data, dict) else []
            if isinstance(members, list):
                for member in members:
                    if not isinstance(member, dict):
                        continue
                    is_active = bool(member.get("isActive", False))
                    agent_id = str(member.get("agentId", "") or "").strip()
                    name = str(member.get("name", "") or "").strip()
                    if agent_id:
                        owner_activity[agent_id] = is_active
                    if name:
                        owner_activity[name] = is_active

        records = [record for record in board.list() if not record.metadata.get("_internal")]
        resolved_ids = {record.id for record in records if record.status == "completed"}
        runtime_plan_state = runtime_snapshot.get("planState", {})
        if not isinstance(runtime_plan_state, dict):
            runtime_plan_state = {}

        payload_tasks: list[dict[str, Any]] = []
        for record in records:
            if not include_completed and record.status == "completed":
                continue
            item = record.to_dict()
            item["blockedBy"] = [value for value in record.blockedBy if value not in resolved_ids]
            if record.owner:
                item["ownerIsActive"] = owner_activity.get(record.owner, record.status == "in_progress")
            payload_tasks.append(item)

        return web.json_response(
            {
                "task_list_id": resolved_task_list_id or "",
                "tasks": payload_tasks,
                "include_completed": include_completed,
                "backgroundTasks": runtime_snapshot.get("backgroundTasks", []),
                "team": runtime_snapshot.get("team", {}),
                "planState": runtime_plan_state,
            }
        )

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

    @staticmethod
    def _compat_sse_frame(payload: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")

    def _compat_stream_payloads(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        event_type = str(payload.get("event_type", "") or "")
        if event_type in {"text", "assistant_delta", "text_delta"}:
            text = str(payload.get("text", "") or "")
            if not text:
                return []
            return [{
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": text},
            }]
        if event_type == "thinking":
            phase = str(payload.get("phase", "") or "")
            text = str(payload.get("text", "") or "")
            frames: list[dict[str, Any]] = []
            if phase == "start":
                frames.append({
                    "type": "content_block_start",
                    "content_block": {"type": "thinking"},
                })
            if text:
                frames.append({
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": text},
                })
            return frames
        if event_type == "tool_call":
            return [{
                "type": "tool_use_start",
                "tool_use_id": str(payload.get("tool_use_id", "") or ""),
                "tool_name": str(payload.get("tool_name", "") or "unknown"),
                "tool_input": payload.get("tool_input", {}) if isinstance(payload.get("tool_input"), dict) else {},
            }]
        if event_type == "pending_tool_call":
            calls = payload.get("calls", [])
            if not isinstance(calls, list):
                return []
            return [
                {
                    "type": "tool_use_start",
                    "tool_use_id": str(call.get("tool_use_id", "") or ""),
                    "tool_name": str(call.get("tool_name", "") or "unknown"),
                    "tool_input": call.get("tool_input", {}) if isinstance(call.get("tool_input"), dict) else {},
                }
                for call in calls
            ]
        if event_type == "tool_result":
            return [{
                "type": "tool_use_done",
                "tool_use_id": str(payload.get("tool_use_id", "") or ""),
                "tool_name": str(payload.get("tool_name", "") or "unknown"),
                "content": str(payload.get("result", "") or ""),
                "is_error": bool(payload.get("is_error", False)),
            }]
        if event_type == "tool_progress":
            message = str(payload.get("content", "") or "").strip()
            if not message:
                return []
            return [{"type": "status", "message": message}]
        if event_type == "thinking_summary":
            return [{
                "type": "thinking_summary",
                "summary": str(payload.get("text", "") or payload.get("summary", "") or ""),
            }]
        if event_type == "status":
            return [{"type": "status", "message": str(payload.get("text", "") or "")}]
        if event_type == "context_size":
            return [{
                "type": "system",
                "event": "context_size",
                "message": "",
                "tokens": int(payload.get("tokens", 0) or 0),
                "limit": int(payload.get("limit", 0) or 0),
            }]
        if event_type == "control_request":
            tool_name = str(payload.get("tool_name", "") or "tool")
            target = str(payload.get("file_path", "") or payload.get("directory_path", "") or "").strip()
            question = f"Allow {tool_name}" + (f" for {target}" if target else "") + "?"
            return [{
                "type": "ask_user",
                "request_id": str(payload.get("request_id", "") or ""),
                "tool_use_id": str(payload.get("tool_use_id", "") or payload.get("request_id", "") or ""),
                "questions": [
                    {
                        "id": "decision",
                        "question": question,
                        "options": [
                            {"label": "allow", "description": "Approve this tool request"},
                            {"label": "deny", "description": "Reject this tool request"},
                        ],
                    }
                ],
            }]
        if event_type == "control_request_resolved":
            return [{
                "type": "control_request_resolved",
                "request_id": str(payload.get("request_id", "") or ""),
                "decision": str(payload.get("decision", "") or ""),
                "reason": str(payload.get("reason", "") or ""),
            }]
        if event_type.startswith("research_"):
            next_payload = dict(payload)
            next_payload["type"] = event_type
            return [next_payload]
        if event_type == "executor_error":
            return [{
                "type": "error",
                "error": str(payload.get("error", "") or "Unknown executor error"),
            }]
        return []

    async def _stream_compat_events(
        self,
        response: Any,
        *,
        session_id: str,
        since: int,
    ) -> Any:
        compat = self._compat_handler
        if compat is None:
            return response

        last_sequence = since
        poll_interval = 0.1
        emitted_text_delta = False
        try:
            while self._running:
                events = compat._stream_events_since(session_id, last_sequence)
                for event in events:
                    seq = int(event.get("sequence_num", 0) or 0)
                    payload = event.get("payload", {})
                    if seq > last_sequence:
                        last_sequence = seq
                    if isinstance(payload, dict):
                        event_type = str(payload.get("event_type", "") or "")
                        if event_type in {"text", "assistant_delta", "text_delta"}:
                            if str(payload.get("text", "") or ""):
                                emitted_text_delta = True
                        elif event_type == "completion":
                            completion_text = str(payload.get("text", "") or "")
                            if completion_text and not emitted_text_delta:
                                await response.write(
                                    self._compat_sse_frame(
                                        {
                                            "type": "content_block_delta",
                                            "delta": {"type": "text_delta", "text": completion_text},
                                        }
                                    )
                                )
                                emitted_text_delta = True
                    for frame_payload in self._compat_stream_payloads(payload if isinstance(payload, dict) else {}):
                        await response.write(self._compat_sse_frame(frame_payload))

                status = compat.get_compat_stream_status(session_id)
                if not bool(status.get("active", False)):
                    await response.write(self._compat_sse_frame({"type": "message_stop"}))
                    await response.write(b"data: [DONE]\n\n")
                    break
                await asyncio.sleep(poll_interval)
        except Exception:
            logger.debug("Compat SSE stream closed", exc_info=True)
        return response

    async def _aiohttp_compat_system_status(self, request: Any) -> Any:
        from aiohttp import web
        import shutil
        import sys

        bash_found = False
        if sys.platform == "win32":
            bash_found = bool(shutil.which("bash") or shutil.which("git-bash"))
        return web.json_response(
            {
                "platform": sys.platform,
                "gitBash": {
                    "required": sys.platform == "win32",
                    "found": bash_found,
                    "path": shutil.which("bash") or shutil.which("git-bash"),
                },
            }
        )

    async def _aiohttp_compat_user_profile(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response(
            {
                "user": {
                    "id": "local-ccmini",
                    "nickname": "ccmini",
                    "email": "",
                    "role": "local",
                }
            }
        )

    async def _aiohttp_compat_update_user_profile(self, request: Any) -> Any:
        from aiohttp import web

        payload = await request.json() if request.can_read_body else {}
        if not isinstance(payload, dict):
            payload = {}
        return web.json_response(payload)

    async def _aiohttp_compat_user_usage(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response(
            {
                "plan": {
                    "id": 999,
                    "name": "ccmini local",
                    "status": "active",
                    "price": 0,
                },
                "token_quota": 99999999,
                "token_remaining": 99999999,
                "used": 0,
                "reset_date": "2099-12-31",
                "is_unlimited": True,
            }
        )

    async def _aiohttp_compat_user_announcements(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"announcements": []})

    async def _aiohttp_compat_mark_announcement_read(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"ok": True})

    async def _aiohttp_compat_user_sessions(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"sessions": []})

    async def _aiohttp_compat_user_delete_session(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"ok": True})

    async def _aiohttp_compat_user_logout_others(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"ok": True})

    async def _aiohttp_compat_user_change_password(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"ok": True})

    async def _aiohttp_compat_user_delete_account(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"ok": True})

    async def _aiohttp_compat_user_models(self, request: Any) -> Any:
        from aiohttp import web
        from ..config import load_config

        compat = self._compat_handler
        items: list[dict[str, Any]] = []
        cfg = load_config()
        default_model = str(getattr(cfg, "model", "") or "").strip()
        if default_model:
            items.append({"id": default_model, "name": default_model, "enabled": True})
        if compat is not None:
            for conversation in compat.list_compat_conversations():
                model = str(conversation.get("model", "") or "").strip()
                if model and not any(existing.get("id") == model for existing in items):
                    items.append({"id": model, "name": model, "enabled": True})
        return web.json_response({"all": items})

    async def _aiohttp_compat_providers(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response([])

    async def _aiohttp_compat_providers_mutation(self, request: Any) -> Any:
        from aiohttp import web

        payload = await request.json() if request.can_read_body else {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("id", str(request.match_info.get("provider_id", "") or "local-provider"))
        payload.setdefault("name", "Local Provider")
        payload.setdefault("models", [])
        payload.setdefault("enabled", True)
        return web.json_response(payload)

    async def _aiohttp_compat_delete_provider(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"ok": True})

    async def _aiohttp_compat_test_provider_websearch(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"ok": False, "reason": "Web search test not configured"})

    async def _aiohttp_compat_provider_models(self, request: Any) -> Any:
        from aiohttp import web
        from ..config import load_config

        compat = self._compat_handler
        models: list[dict[str, Any]] = []
        cfg = load_config()
        default_model = str(getattr(cfg, "model", "") or "").strip()
        if default_model:
            models.append(
                {
                    "id": default_model,
                    "name": default_model,
                    "providerId": "local-provider",
                    "providerName": "Local Provider",
                }
            )
        if compat is not None:
            for conversation in compat.list_compat_conversations():
                model = str(conversation.get("model", "") or "").strip()
                if model and not any(existing.get("id") == model for existing in models):
                    models.append(
                        {
                            "id": model,
                            "name": model,
                            "providerId": "local-provider",
                            "providerName": "Local Provider",
                        }
                    )
        return web.json_response(models)

    async def _aiohttp_compat_projects(self, request: Any) -> Any:
        from aiohttp import web

        path = _compat_projects_path()
        projects = _compat_read_json(path, [])
        if not isinstance(projects, list):
            projects = []
        if request.method == "POST":
            payload = await request.json() if request.can_read_body else {}
            if not isinstance(payload, dict):
                payload = {}
            created_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
            project = {
                "id": f"project-{secrets.token_hex(6)}",
                "name": str(payload.get("name", "") or "Local Project"),
                "description": str(payload.get("description", "") or ""),
                "instructions": "",
                "workspace_path": "",
                "is_archived": 0,
                "created_at": created_at,
                "updated_at": created_at,
                "files": [],
            }
            projects.append(project)
            _compat_write_json(path, projects)
            return web.json_response(project)
        return web.json_response(projects)

    def _compat_find_project(self, project_id: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        path = _compat_projects_path()
        projects = _compat_read_json(path, [])
        if not isinstance(projects, list):
            projects = []
        for project in projects:
            if str(project.get("id", "") or "") == project_id:
                return projects, project
        return projects, None

    async def _aiohttp_compat_project_detail(self, request: Any) -> Any:
        from aiohttp import web

        project_id = str(request.match_info.get("project_id", "") or "").strip()
        path = _compat_projects_path()
        projects, project = self._compat_find_project(project_id)
        if project is None:
            return web.json_response({"error": "Project not found"}, status=404)

        if request.method == "PATCH":
            payload = await request.json() if request.can_read_body else {}
            if not isinstance(payload, dict):
                payload = {}
            for key in ("name", "description", "instructions", "is_archived"):
                if key in payload:
                    project[key] = payload[key]
            project["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
            _compat_write_json(path, projects)
        elif request.method == "DELETE":
            projects = [item for item in projects if str(item.get("id", "")) != project_id]
            _compat_write_json(path, projects)
            return web.json_response({"ok": True})

        conversations = [
            item for item in self._compat_handler.list_compat_conversations()
            if str(item.get("project_id", "") or "") == project_id
        ]
        project = dict(project)
        project.setdefault("files", [])
        project["file_count"] = len(project.get("files", []))
        project["chat_count"] = len(conversations)
        project["conversations"] = conversations
        return web.json_response(project)

    async def _aiohttp_compat_project_upload_file(self, request: Any) -> Any:
        from aiohttp import web

        project_id = str(request.match_info.get("project_id", "") or "").strip()
        path = _compat_projects_path()
        projects, project = self._compat_find_project(project_id)
        if project is None:
            return web.json_response({"error": "Project not found"}, status=404)

        reader = await request.multipart()
        part = await reader.next()
        if part is None or part.name != "file":
            return web.json_response({"error": "Missing file"}, status=400)

        filename = part.filename or f"upload-{secrets.token_hex(4)}"
        file_id = secrets.token_hex(8)
        project_dir = _compat_root() / "projects" / project_id / "files"
        project_dir.mkdir(parents=True, exist_ok=True)
        target = project_dir / f"{file_id}-{filename}"
        size = 0
        with target.open("wb") as handle:
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                handle.write(chunk)

        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        entry = {
            "id": file_id,
            "project_id": project_id,
            "file_name": filename,
            "file_path": str(target),
            "file_size": size,
            "mime_type": mime_type,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        }
        files = project.setdefault("files", [])
        files.append(entry)
        project["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        _compat_write_json(path, projects)
        return web.json_response(entry)

    async def _aiohttp_compat_project_delete_file(self, request: Any) -> Any:
        from aiohttp import web

        project_id = str(request.match_info.get("project_id", "") or "").strip()
        file_id = str(request.match_info.get("file_id", "") or "").strip()
        path = _compat_projects_path()
        projects, project = self._compat_find_project(project_id)
        if project is None:
            return web.json_response({"error": "Project not found"}, status=404)
        files = project.setdefault("files", [])
        kept = []
        for item in files:
            if str(item.get("id", "") or "") == file_id:
                with contextlib.suppress(Exception):
                    Path(str(item.get("file_path", "") or "")).unlink(missing_ok=True)
                continue
            kept.append(item)
        project["files"] = kept
        project["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        _compat_write_json(path, projects)
        return web.json_response({"ok": True})

    async def _aiohttp_compat_project_conversations(self, request: Any) -> Any:
        from aiohttp import web

        project_id = str(request.match_info.get("project_id", "") or "").strip()
        conversations = [
            item for item in self._compat_handler.list_compat_conversations()
            if str(item.get("project_id", "") or "") == project_id
        ]
        return web.json_response(conversations)

    async def _aiohttp_compat_project_create_conversation(self, request: Any) -> Any:
        from aiohttp import web

        project_id = str(request.match_info.get("project_id", "") or "").strip()
        payload = await request.json() if request.can_read_body else {}
        if not isinstance(payload, dict):
            payload = {}
        conversation = await self._compat_handler.create_compat_conversation(
            title=str(payload.get("title", "") or ""),
            model=str(payload.get("model", "") or ""),
            research_mode=False,
        )
        self._compat_handler.update_compat_conversation(conversation["id"], {"project_id": project_id})
        updated = self._compat_handler.get_compat_conversation(conversation["id"]) or conversation
        return web.json_response(updated)

    async def _aiohttp_compat_skills(self, request: Any) -> Any:
        from aiohttp import web
        compat = self._compat_handler
        if compat is not None and hasattr(compat, "list_compat_skills"):
            data = compat.list_compat_skills()
            examples = list(data.get("examples", [])) if isinstance(data, dict) else []
            runtime_skills = list(data.get("my_skills", [])) if isinstance(data, dict) else []
            custom_skills = _compat_load_custom_skills()
            return web.json_response({"examples": examples, "my_skills": runtime_skills + custom_skills})
        return web.json_response({"examples": [], "my_skills": _compat_load_custom_skills()})

    async def _aiohttp_compat_create_skill(self, request: Any) -> Any:
        from aiohttp import web

        path = _compat_skills_path()
        skills = _compat_read_json(path, [])
        if not isinstance(skills, list):
            skills = []
        payload = await request.json() if request.can_read_body else {}
        if not isinstance(payload, dict):
            payload = {}
        skill_id = f"skill-{secrets.token_hex(6)}"
        created_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        skill = {
            "id": skill_id,
            "name": str(payload.get("name", "") or "Untitled Skill"),
            "description": str(payload.get("description", "") or ""),
            "content": str(payload.get("content", "") or ""),
            "enabled": True,
            "created_at": created_at,
            "files": [{"name": "SKILL.md", "type": "file"}],
        }
        skills.append(skill)
        _compat_write_json(path, skills)
        return web.json_response(skill)

    def _compat_find_skill(self, skill_id: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        skills = _compat_load_custom_skills()
        for skill in skills:
            if str(skill.get("id", "") or "") == skill_id:
                return skills, skill
        return skills, None

    def _compat_find_custom_skill(self, skill_id: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        skills = _compat_load_custom_skills()
        for skill in skills:
            if str(skill.get("id", "") or "") == skill_id:
                return skills, skill
        return skills, None

    async def _aiohttp_compat_skill_detail(self, request: Any) -> Any:
        from aiohttp import web

        skill_id = str(request.match_info.get("skill_id", "") or "").strip()
        compat = self._compat_handler
        if compat is not None and hasattr(compat, "get_compat_skill"):
            skill = compat.get_compat_skill(skill_id)
            if skill is not None:
                return web.json_response(skill)
        _, skill = self._compat_find_skill(skill_id)
        if skill is None:
            return web.json_response({"error": "Skill not found"}, status=404)
        return web.json_response(skill)

    async def _aiohttp_compat_skill_file(self, request: Any) -> Any:
        from aiohttp import web

        skill_id = str(request.match_info.get("skill_id", "") or "").strip()
        file_path = str(request.query.get("path", "") or "SKILL.md").strip() or "SKILL.md"
        compat = self._compat_handler
        if compat is not None and hasattr(compat, "get_compat_skill_file"):
            content = compat.get_compat_skill_file(skill_id, file_path)
            if content is not None:
                return web.json_response({"content": content})
        _, skill = self._compat_find_skill(skill_id)
        if skill is None:
            return web.json_response({"error": "Skill not found"}, status=404)
        return web.json_response({"content": str(skill.get("content", "") or "")})

    async def _aiohttp_compat_update_skill(self, request: Any) -> Any:
        from aiohttp import web

        skill_id = str(request.match_info.get("skill_id", "") or "").strip()
        path = _compat_skills_path()
        skills, skill = self._compat_find_custom_skill(skill_id)
        if skill is None:
            return web.json_response({"error": "Skill is read-only"}, status=403)
        payload = await request.json() if request.can_read_body else {}
        if not isinstance(payload, dict):
            payload = {}
        for key in ("name", "description", "content"):
            if key in payload:
                skill[key] = payload[key]
        _compat_write_json(path, skills)
        return web.json_response(skill)

    async def _aiohttp_compat_delete_skill(self, request: Any) -> Any:
        from aiohttp import web

        skill_id = str(request.match_info.get("skill_id", "") or "").strip()
        path = _compat_skills_path()
        skills = _compat_read_json(path, [])
        if not isinstance(skills, list):
            skills = []
        if not any(str(skill.get("id", "") or "") == skill_id for skill in skills if isinstance(skill, dict)):
            return web.json_response({"error": "Skill is read-only"}, status=403)
        skills = [skill for skill in skills if str(skill.get("id", "") or "") != skill_id]
        _compat_write_json(path, skills)
        return web.json_response({"ok": True})

    async def _aiohttp_compat_toggle_skill(self, request: Any) -> Any:
        from aiohttp import web

        skill_id = str(request.match_info.get("skill_id", "") or "").strip()
        path = _compat_skills_path()
        payload = await request.json() if request.can_read_body else {}
        enabled = bool(payload.get("enabled", False)) if isinstance(payload, dict) else False
        skills, skill = self._compat_find_custom_skill(skill_id)
        if skill is not None:
            skill["enabled"] = enabled
            raw_skills = _compat_read_json(path, [])
            if not isinstance(raw_skills, list):
                raw_skills = []
            for raw_skill in raw_skills:
                if isinstance(raw_skill, dict) and str(raw_skill.get("id", "") or "") == skill_id:
                    raw_skill["enabled"] = enabled
            _compat_write_json(path, raw_skills)
            return web.json_response(skill)

        compat = self._compat_handler
        compat_skill = None
        if compat is not None and hasattr(compat, "get_compat_skill"):
            compat_skill = compat.get_compat_skill(skill_id)
        if compat_skill is None:
            return web.json_response({"error": "Skill not found"}, status=404)
        overrides = _compat_read_skill_overrides()
        current = overrides.get(skill_id, {})
        if not isinstance(current, dict):
            current = {}
        current["enabled"] = enabled
        overrides[skill_id] = current
        _compat_write_skill_overrides(overrides)
        compat_skill["enabled"] = enabled
        return web.json_response(compat_skill)

    async def _aiohttp_compat_github_status(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"connected": False, "user": None})

    async def _aiohttp_compat_github_auth_url(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"url": "https://github.com/login"})

    async def _aiohttp_compat_github_disconnect(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"ok": True})

    async def _aiohttp_compat_github_repos(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response([])

    async def _aiohttp_compat_github_tree(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"tree": []})

    async def _aiohttp_compat_github_contents(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response([])

    async def _aiohttp_compat_github_materialize(self, request: Any) -> Any:
        from aiohttp import web

        payload = await request.json() if request.can_read_body else {}
        if not isinstance(payload, dict):
            payload = {}
        return web.json_response(
            {
                "ok": True,
                "repoFullName": str(payload.get("repoFullName", "") or ""),
                "ref": str(payload.get("ref", "") or "main"),
                "rootDir": "",
                "fileCount": 0,
                "skipped": 0,
            }
        )

    async def _aiohttp_compat_list_conversations(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response(self._compat_handler.list_compat_conversations())

    async def _aiohttp_compat_create_conversation(self, request: Any) -> Any:
        from aiohttp import web

        payload = await request.json() if request.can_read_body else {}
        if not isinstance(payload, dict):
            payload = {}
        data = await self._compat_handler.create_compat_conversation(
            title=str(payload.get("title", "") or ""),
            model=str(payload.get("model", "") or ""),
            research_mode=bool(payload.get("research_mode", False)),
            workspace_path=str(payload.get("workspace_path", "") or ""),
        )
        return web.json_response(data)

    async def _aiohttp_compat_get_conversation(self, request: Any) -> Any:
        from aiohttp import web

        session_id = str(request.match_info.get("session_id", "") or "").strip()
        data = self._compat_handler.get_compat_conversation(session_id)
        if data is None:
            return web.json_response({"error": "Conversation not found"}, status=404)
        return web.json_response(data)

    async def _aiohttp_compat_update_conversation(self, request: Any) -> Any:
        from aiohttp import web

        session_id = str(request.match_info.get("session_id", "") or "").strip()
        payload = await request.json() if request.can_read_body else {}
        if not isinstance(payload, dict):
            payload = {}
        data = self._compat_handler.update_compat_conversation(session_id, payload)
        if data is None:
            return web.json_response({"error": "Conversation not found"}, status=404)
        return web.json_response(data)

    async def _aiohttp_compat_delete_conversation(self, request: Any) -> Any:
        from aiohttp import web

        session_id = str(request.match_info.get("session_id", "") or "").strip()
        await self._compat_handler.delete_compat_conversation(session_id)
        return web.json_response({"ok": True})

    async def _aiohttp_compat_generation_status(self, request: Any) -> Any:
        from aiohttp import web

        session_id = str(request.match_info.get("session_id", "") or "").strip()
        return web.json_response(self._compat_handler.get_compat_generation_status(session_id))

    async def _aiohttp_compat_stop_generation(self, request: Any) -> Any:
        from aiohttp import web

        session_id = str(request.match_info.get("session_id", "") or "").strip()
        data = await self._compat_handler.stop_compat_generation(session_id)
        return web.json_response(data)

    async def _aiohttp_compat_context_size(self, request: Any) -> Any:
        from aiohttp import web

        session_id = str(request.match_info.get("session_id", "") or "").strip()
        return web.json_response(self._compat_handler.get_compat_context_size(session_id))

    async def _aiohttp_compat_stream_status(self, request: Any) -> Any:
        from aiohttp import web

        session_id = str(request.match_info.get("session_id", "") or "").strip()
        return web.json_response(self._compat_handler.get_compat_stream_status(session_id))

    async def _aiohttp_compat_reconnect(self, request: Any) -> Any:
        from aiohttp import web

        session_id = str(request.match_info.get("session_id", "") or "").strip()
        state = getattr(self._compat_handler, "_sessions", {}).get(session_id)
        if state is None:
            return web.json_response({"error": "Conversation not found"}, status=404)

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        return await self._stream_compat_events(
            response,
            session_id=session_id,
            since=int(getattr(state, "active_stream_since", 0) or 0),
        )

    async def _aiohttp_compat_answer(self, request: Any) -> Any:
        from aiohttp import web

        session_id = str(request.match_info.get("session_id", "") or "").strip()
        payload = await request.json() if request.can_read_body else {}
        if not isinstance(payload, dict):
            payload = {}
        data = await self._compat_handler.answer_compat_question(
            session_id=session_id,
            request_id=str(payload.get("request_id", "") or ""),
            tool_use_id=str(payload.get("tool_use_id", "") or ""),
            answers=payload.get("answers", {}) if isinstance(payload.get("answers"), dict) else {},
        )
        return web.json_response(data)

    async def _aiohttp_compat_warm(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"ok": True})

    async def _aiohttp_compat_compact(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"summary": "", "tokensSaved": 0, "messagesCompacted": 0})

    async def _aiohttp_compat_delete_messages_from(self, request: Any) -> Any:
        from aiohttp import web

        session_id = str(request.match_info.get("session_id", "") or "").strip()
        message_id = str(request.match_info.get("message_id", "") or "").strip()
        conversation = self._compat_handler.get_compat_conversation(session_id)
        if conversation is None:
            return web.json_response({"error": "Conversation not found"}, status=404)
        messages = self._compat_handler._get_session_messages(session_id)
        cutoff = None
        for index, message in enumerate(messages):
            if str(message.metadata.get("uuid", "") or "") == message_id:
                cutoff = index
                break
        if cutoff is None:
            return web.json_response({"ok": False, "error": "Message not found"}, status=404)
        trimmed = messages[:cutoff]
        self._compat_handler._session_store.save_messages(session_id, trimmed)
        meta = self._compat_handler._load_session_metadata(session_id)
        meta.message_count = len(trimmed)
        self._compat_handler._save_session_metadata(meta)
        return web.json_response({"ok": True})

    async def _aiohttp_compat_delete_messages_tail(self, request: Any) -> Any:
        from aiohttp import web

        session_id = str(request.match_info.get("session_id", "") or "").strip()
        try:
            count = max(0, int(request.match_info.get("count", "0") or 0))
        except ValueError:
            count = 0
        conversation = self._compat_handler.get_compat_conversation(session_id)
        if conversation is None:
            return web.json_response({"error": "Conversation not found"}, status=404)
        messages = self._compat_handler._get_session_messages(session_id)
        trimmed = messages[:-count] if count > 0 else messages
        self._compat_handler._session_store.save_messages(session_id, trimmed)
        meta = self._compat_handler._load_session_metadata(session_id)
        meta.message_count = len(trimmed)
        self._compat_handler._save_session_metadata(meta)
        return web.json_response({"ok": True})

    async def _aiohttp_compat_chat(self, request: Any) -> Any:
        from aiohttp import web

        payload = await request.json() if request.can_read_body else {}
        if not isinstance(payload, dict):
            payload = {}
        session_id = str(payload.get("conversation_id", "") or "").strip()
        if not session_id:
            return web.json_response({"error": "conversation_id is required"}, status=400)
        message = str(payload.get("message", "") or "")
        attachments = payload.get("attachments", [])
        if not isinstance(attachments, list):
            attachments = []

        start = await self._compat_handler.start_compat_chat(
            session_id=session_id,
            message=message,
            attachments=attachments,
        )
        if str(start.get("status", "")) == "busy":
            return web.json_response({"error": "Conversation is busy"}, status=409)

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        return await self._stream_compat_events(
            response,
            session_id=session_id,
            since=int(start.get("stream_since", 0) or 0),
        )

    async def _aiohttp_compat_upload(self, request: Any) -> Any:
        from aiohttp import web

        reader = await request.multipart()
        part = await reader.next()
        if part is None or part.name != "file":
            return web.json_response({"error": "Missing file"}, status=400)
        filename = part.filename or f"upload-{secrets.token_hex(4)}"
        file_id = secrets.token_hex(8)
        target = _compat_uploads_dir() / f"{file_id}-{filename}"
        size = 0
        with target.open("wb") as handle:
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                handle.write(chunk)
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if mime_type.startswith("image/"):
            file_type = "image"
        elif mime_type.startswith("text/"):
            file_type = "text"
        else:
            file_type = "document"
        meta_path = _compat_uploads_meta_path()
        uploads = _compat_read_json(meta_path, {})
        if not isinstance(uploads, dict):
            uploads = {}
        uploads[file_id] = {
            "fileId": file_id,
            "fileName": filename,
            "fileType": file_type,
            "mimeType": mime_type,
            "size": size,
            "path": str(target),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        }
        _compat_write_json(meta_path, uploads)
        return web.json_response(uploads[file_id])

    async def _aiohttp_compat_delete_upload(self, request: Any) -> Any:
        from aiohttp import web

        file_id = str(request.match_info.get("file_id", "") or "").strip()
        meta_path = _compat_uploads_meta_path()
        uploads = _compat_read_json(meta_path, {})
        if not isinstance(uploads, dict):
            uploads = {}
        item = uploads.pop(file_id, None)
        if item is not None:
            with contextlib.suppress(Exception):
                Path(str(item.get("path", "") or "")).unlink(missing_ok=True)
            _compat_write_json(meta_path, uploads)
        return web.json_response({"ok": True})

    async def _aiohttp_compat_upload_path(self, request: Any) -> Any:
        from aiohttp import web

        file_id = str(request.match_info.get("file_id", "") or "").strip()
        uploads = _compat_read_json(_compat_uploads_meta_path(), {})
        if not isinstance(uploads, dict):
            uploads = {}
        item = uploads.get(file_id)
        if not item:
            return web.json_response({"error": "Upload not found"}, status=404)
        local_path = str(item.get("path", "") or "")
        return web.json_response(
            {
                "localPath": local_path,
                "folder": str(Path(local_path).parent) if local_path else "",
            }
        )

    async def _aiohttp_compat_upload_raw(self, request: Any) -> Any:
        from aiohttp import web

        file_id = str(request.match_info.get("file_id", "") or "").strip()
        meta_path = _compat_uploads_meta_path()
        uploads = _compat_read_json(meta_path, {})
        if not isinstance(uploads, dict):
            uploads = {}
        item = uploads.get(file_id)
        if not item:
            return web.json_response({"error": "Upload not found"}, status=404)
        file_path = Path(str(item.get("path", "") or ""))
        if not file_path.exists():
            return web.json_response({"error": "Upload not found"}, status=404)
        return web.FileResponse(path=file_path, headers={"Content-Type": str(item.get("mimeType", "application/octet-stream"))})

    async def _aiohttp_compat_document_raw(self, request: Any) -> Any:
        from aiohttp import web

        document_id = str(request.match_info.get("document_id", "") or "").strip()
        if not document_id:
            return web.json_response({"error": "Document not found"}, status=404)

        uploads = _compat_read_json(_compat_uploads_meta_path(), {})
        if isinstance(uploads, dict) and document_id in uploads:
            item = uploads.get(document_id) or {}
            file_path = Path(str(item.get("path", "") or ""))
            if file_path.exists():
                return web.FileResponse(path=file_path, headers={"Content-Type": str(item.get("mimeType", "application/octet-stream"))})

        projects = _compat_read_json(_compat_projects_path(), [])
        if isinstance(projects, list):
            for project in projects:
                for entry in project.get("files", []) if isinstance(project.get("files"), list) else []:
                    if str(entry.get("id", "") or "") != document_id:
                        continue
                    file_path = Path(str(entry.get("file_path", "") or ""))
                    if file_path.exists():
                        mime_type = str(entry.get("mime_type", "") or "application/octet-stream")
                        return web.FileResponse(path=file_path, headers={"Content-Type": mime_type})

        return web.json_response({"error": "Document not found"}, status=404)

    async def _aiohttp_compat_artifacts(self, request: Any) -> Any:
        from aiohttp import web
        artifacts: list[dict[str, Any]] = []

        uploads = _compat_read_json(_compat_uploads_meta_path(), {})
        if isinstance(uploads, dict):
            for file_id, item in uploads.items():
                filename = str(item.get("fileName", "") or "")
                mime_type = str(item.get("mimeType", "") or "")
                if not _compat_is_artifact_candidate(filename, mime_type):
                    continue
                artifacts.append(
                    {
                        "id": file_id,
                        "title": filename,
                        "file_path": str(item.get("path", "") or ""),
                        "mime_type": mime_type,
                        "created_at": str(item.get("created_at", "") or ""),
                    }
                )

        projects = _compat_read_json(_compat_projects_path(), [])
        if isinstance(projects, list):
            for project in projects:
                for entry in project.get("files", []) if isinstance(project.get("files"), list) else []:
                    filename = str(entry.get("file_name", "") or "")
                    mime_type = str(entry.get("mime_type", "") or "")
                    if not _compat_is_artifact_candidate(filename, mime_type):
                        continue
                    artifacts.append(
                        {
                            "id": str(entry.get("id", "") or ""),
                            "title": filename,
                            "file_path": str(entry.get("file_path", "") or ""),
                            "mime_type": mime_type,
                            "created_at": str(entry.get("created_at", "") or ""),
                        }
                    )

        artifacts.sort(key=lambda item: str(item.get("created_at", "") or ""), reverse=True)
        return web.json_response(artifacts)

    async def _aiohttp_compat_artifact_content(self, request: Any) -> Any:
        from aiohttp import web
        requested = str(request.query.get("path", "") or "").strip()
        if not requested:
            return web.json_response({"error": "Missing path"}, status=400)
        target = Path(requested)
        if not target.exists() or not target.is_file():
            return web.json_response({"error": "Artifact not found"}, status=404)
        try:
            content = target.read_text(encoding="utf-8")
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"content": content, "path": str(target)})

    async def _aiohttp_compat_code_sso(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"enabled": False, "url": ""})

    async def _aiohttp_compat_code_quota(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response({"limit": 0, "used": 0, "remaining": 0, "enabled": False})

    async def _aiohttp_compat_code_plans(self, request: Any) -> Any:
        from aiohttp import web

        return web.json_response([])

    async def _aiohttp_task_control(self, request: Any) -> Any:
        from aiohttp import web

        if not self._is_http_authorized(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            payload = await request.json()
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

        if not isinstance(payload, dict):
            return web.json_response({"error": "Request body must be a JSON object"}, status=400)

        session_id = str(payload.get("session_id", "") or "").strip()
        task_id = str(payload.get("task_id", "") or payload.get("taskId", "") or "").strip()
        action = str(payload.get("action", "") or "").strip()
        extra = payload.get("payload", {})
        if not session_id or not action:
            return web.json_response({"error": "session_id and action are required"}, status=400)
        if action != "reset_task_list_if_completed" and not task_id:
            return web.json_response({"error": "task_id is required for this action"}, status=400)
        if not isinstance(extra, dict):
            extra = {}

        result = await self._api.control_runtime_task(
            session_id,
            task_id=task_id,
            action=action,
            payload=extra,
        )
        status = 200 if bool(result.get("ok", False)) else 400
        return web.json_response(result, status=status)

    async def _aiohttp_task_transcript(self, request: Any) -> Any:
        from aiohttp import web

        if not self._is_http_authorized(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        session_id = str(request.query.get("session_id", "") or "").strip()
        task_id = str(request.query.get("task_id", "") or "").strip()
        try:
            limit = max(1, min(1000, int(request.query.get("limit", "200") or 200)))
        except ValueError:
            limit = 200
        if not session_id or not task_id:
            return web.json_response({"error": "session_id and task_id are required"}, status=400)

        result = self._api.get_runtime_transcript(session_id, task_id=task_id, limit=limit)
        status = 200 if bool(result.get("ok", False)) else 404
        return web.json_response(result, status=status)

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
