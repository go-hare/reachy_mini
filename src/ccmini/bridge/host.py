"""Remote executor host for exposing ccmini Agent instances over the bridge."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..agent import Agent, AgentConfig
from ..config import load_config
from ..messages import (
    CompletionEvent,
    ErrorEvent,
    Message,
    PendingToolCallEvent,
    PromptSuggestionEvent,
    RequestStartEvent,
    SpeculationEvent,
    TextBlock,
    ThinkingEvent,
    TextEvent,
    ToolResultBlock,
    ToolCallEvent,
    ToolProgressEvent,
    ToolResultEvent,
    ToolUseSummaryEvent,
    ToolUseBlock,
    UsageEvent,
    tool_result_content_to_text,
)
from ..permissions import PermissionDecision, PermissionMode, PermissionRule, build_permission_checker
from ..profiles import RuntimeProfile
from ..prompts import SystemPrompt
from ..providers import BaseProvider, ProviderConfig
from ..session.store import SessionMetadata, SessionStore
from ..tool import ClientTool, Tool, ToolResult, ToolUseContext, find_tool_by_name
from .api import BridgeAPI
from .core import BridgeConfig, BridgeServer
from .net_utils import build_connect_url
from .webrtc_host import WebRTCExecutorManager


def _load_default_bridge_config() -> BridgeConfig:
    from ..config import load_config

    cfg = load_config()
    return BridgeConfig(
        enabled=True,
        host=cfg.ccmini_host or "127.0.0.1",
        port=cfg.ccmini_port or 7779,
        auth_token=cfg.ccmini_auth_token,
    )


def _serialize_stream_event(event: Any) -> dict[str, Any]:
    """Convert ccmini stream events into JSON-safe bridge payloads."""
    base: dict[str, Any] = {}
    for key in ("conversation_id", "turn_id", "run_id", "tool_use_id"):
        value = str(getattr(event, key, "") or "").strip()
        if value:
            base[key] = value
    metadata = getattr(event, "metadata", None)
    if isinstance(metadata, dict) and metadata:
        base["metadata"] = dict(metadata)

    if isinstance(event, RequestStartEvent):
        return {"event_type": "request_start", **base}
    if isinstance(event, ThinkingEvent):
        return {
            "event_type": "thinking",
            "text": event.text,
            "is_redacted": event.is_redacted,
            "phase": event.phase,
            "source": event.source,
            "signature": event.signature,
            **base,
        }
    if isinstance(event, TextEvent):
        metadata_event_type = ""
        if isinstance(metadata, dict):
            metadata_event_type = str(metadata.get("event_type", "") or "").strip()
        payload: dict[str, Any] = {
            "event_type": metadata_event_type or "text",
            "text": event.text,
            **base,
        }
        if isinstance(metadata, dict) and metadata_event_type == "task_state":
            task_state = metadata.get("task_state")
            if isinstance(task_state, dict):
                payload["task_state"] = dict(task_state)
        return payload
    if isinstance(event, ToolCallEvent):
        return {
            "event_type": "tool_call",
            "tool_use_id": event.tool_use_id,
            "tool_name": event.tool_name,
            "tool_input": event.tool_input,
            **base,
        }
    if isinstance(event, ToolResultEvent):
        return {
            "event_type": "tool_result",
            "tool_use_id": event.tool_use_id,
            "tool_name": event.tool_name,
            "result": event.result,
            "is_error": event.is_error,
            **base,
        }
    if isinstance(event, ToolProgressEvent):
        return {
            "event_type": "tool_progress",
            "tool_use_id": event.tool_use_id,
            "tool_name": event.tool_name,
            "content": event.content,
            **base,
        }
    if isinstance(event, ToolUseSummaryEvent):
        return {
            "event_type": "tool_use_summary",
            "summary": event.summary,
            "tool_use_ids": list(event.tool_use_ids),
            **base,
        }
    if isinstance(event, PromptSuggestionEvent):
        return {
            "event_type": "prompt_suggestion",
            "text": event.text,
            "shown_at": event.shown_at,
            "accepted_at": event.accepted_at,
            **base,
        }
    if isinstance(event, SpeculationEvent):
        return {
            "event_type": "speculation",
            "status": event.status,
            "suggestion": event.suggestion,
            "reply": event.reply,
            "started_at": event.started_at,
            "completed_at": event.completed_at,
            "error": event.error,
            "boundary": dict(event.boundary),
            **base,
        }
    if isinstance(event, PendingToolCallEvent):
        return {
            "event_type": "pending_tool_call",
            "run_id": event.run_id,
            "calls": [
                {
                    "tool_use_id": call.tool_use_id,
                    "tool_name": call.tool_name,
                    "tool_input": call.tool_input,
                    "conversation_id": call.conversation_id,
                    "turn_id": call.turn_id,
                    "run_id": call.run_id,
                }
                for call in event.calls
            ],
            **base,
        }
    if isinstance(event, UsageEvent):
        return {
            "event_type": "usage",
            "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens,
            "cache_read_tokens": event.cache_read_tokens,
            "cache_creation_tokens": event.cache_creation_tokens,
            "model": event.model,
            "stop_reason": event.stop_reason,
            **base,
        }
    if isinstance(event, CompletionEvent):
        return {
            "event_type": "completion",
            "text": event.text,
            "stop_reason": event.stop_reason,
            **base,
        }
    if isinstance(event, ErrorEvent):
        return {
            "event_type": "error",
            "error": event.error,
            "recoverable": event.recoverable,
            **base,
        }
    return {
        "event_type": getattr(event, "type", event.__class__.__name__.lower()),
        "repr": repr(event),
        **base,
    }


@dataclass(slots=True)
class RemoteExecutorSessionHandle:
    session_id: str
    base_url: str
    auth_token: str
    websocket_url: str


@dataclass(slots=True)
class _ExecutorSessionState:
    agent: Agent
    started: bool = False
    active_query: asyncio.Task[None] | None = None
    active_stream_since: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    unsubscribe: Callable[[], None] | None = None
    research_mode: bool = False
    project_id: str = ""
    pending_control_requests: dict[str, asyncio.Future[dict[str, Any]]] = field(
        default_factory=dict,
    )


class _ExecutorBridgeAPI(BridgeAPI):
    def __init__(self, host: "RemoteExecutorHost") -> None:
        self._host = host
        super().__init__(
            on_query=self._host._handle_query,
            on_tool_call=self._host._handle_tool_call,
            on_submit_tool_results=self._host._handle_submit_tool_results,
            on_control_response=self._host._handle_control_response,
        )

    def create_session(self, metadata: dict[str, Any] | None = None) -> str:
        session_id = super().create_session(metadata)
        self._host._ensure_session_state(session_id)
        return session_id

    def end_session(self, session_id: str) -> bool:
        existed = super().end_session(session_id)
        if existed:
            self._host._shutdown_session(session_id)
        return existed

    def remove_session(self, session_id: str) -> bool:
        existed = super().remove_session(session_id)
        if existed:
            self._host._shutdown_session(session_id)
        return existed

    def get_runtime_snapshot(self, session_id: str) -> dict[str, Any] | None:
        return self._host._get_runtime_snapshot(session_id)

    async def control_runtime_task(
        self,
        session_id: str,
        *,
        task_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._host._control_runtime_task(
            session_id,
            task_id=task_id,
            action=action,
            payload=payload,
        )

    def get_runtime_transcript(
        self,
        session_id: str,
        *,
        task_id: str,
        limit: int = 200,
    ) -> dict[str, Any]:
        return self._host._get_runtime_transcript(
            session_id,
            task_id=task_id,
            limit=limit,
        )


class RemoteExecutorHost:
    """Owns bridge server + per-session ccmini agents for remote UIs."""

    def __init__(
        self,
        *,
        agent_factory: Callable[[str], Agent],
        bridge_config: BridgeConfig | None = None,
    ) -> None:
        self._agent_factory = agent_factory
        self._sessions: dict[str, _ExecutorSessionState] = {}
        self._session_store = SessionStore()
        self._api = _ExecutorBridgeAPI(self)
        self._server = BridgeServer(
            bridge_config or BridgeConfig(enabled=True),
            api=self._api,
            compat_handler=self,
        )
        self._webrtc = WebRTCExecutorManager(self._api)

    @property
    def api(self) -> BridgeAPI:
        return self._api

    @property
    def server(self) -> BridgeServer:
        return self._server

    @property
    def config(self) -> BridgeConfig:
        return self._server.config

    async def start(self) -> None:
        await self._server.start()
        self._webrtc.start()

    async def stop(self) -> None:
        for session_id in list(self._sessions):
            await self._shutdown_session_async(session_id)
        await self._webrtc.stop()
        await self._server.stop()

    async def create_session(
        self,
        metadata: dict[str, Any] | None = None,
    ) -> RemoteExecutorSessionHandle:
        session_id = self._api.create_session(metadata)
        await self._ensure_started(session_id)
        base_url = build_connect_url(
            host=self.config.host,
            port=self.config.port,
            ssl=self.config.ssl,
        )
        websocket_url = build_connect_url(
            host=self.config.host,
            port=self.config.port,
            ssl=self.config.ssl,
            websocket=True,
        )
        return RemoteExecutorSessionHandle(
            session_id=session_id,
            base_url=base_url,
            auth_token=self.config.auth_token,
            websocket_url=websocket_url,
        )

    def _load_session_metadata(self, session_id: str) -> SessionMetadata:
        return self._session_store.load_metadata(session_id) or SessionMetadata(
            session_id=session_id,
            created_at=time.time(),
        )

    def _save_session_metadata(self, meta: SessionMetadata) -> None:
        meta.session_id = str(meta.session_id or "").strip()
        if not meta.created_at:
            meta.created_at = time.time()
        meta.updated_at = time.time()
        self._session_store.save_metadata(meta)

    def _sync_session_metadata_from_state(
        self,
        session_id: str,
        state: _ExecutorSessionState | None = None,
    ) -> SessionMetadata:
        meta = self._load_session_metadata(session_id)
        if state is not None:
            title = str(getattr(state.agent, "_session_name", "") or "").strip()
            if title:
                meta.title = title
            meta.model = str(getattr(getattr(state.agent, "provider", None), "model_name", "") or meta.model)
            meta.research_mode = bool(state.research_mode)
            meta.project_id = str(state.project_id or "")
            meta.cwd = str(getattr(state.agent, "working_directory", "") or meta.cwd)
        self._save_session_metadata(meta)
        return meta

    @staticmethod
    def _iso_timestamp(value: Any) -> str:
        try:
            ts = float(value or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts <= 0:
            ts = time.time()
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))

    @staticmethod
    def _collect_message_text(message: Message) -> str:
        if isinstance(message.content, str):
            return message.content
        parts: list[str] = []
        for block in message.content:
            if isinstance(block, TextBlock) and block.text:
                parts.append(block.text)
        return "\n".join(part for part in parts if part).strip()

    def _convert_messages_to_compat(self, messages: list[Message]) -> list[dict[str, Any]]:
        compat_messages: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                text = self._collect_message_text(message)
                if text:
                    compat_messages.append(
                        {
                            "role": "assistant",
                            "content": text,
                            "created_at": self._iso_timestamp(message.metadata.get("timestamp", time.time())),
                            "isMeta": True,
                        }
                    )
                continue

            if message.role == "user":
                tool_results = message.tool_result_blocks
                if tool_results and compat_messages:
                    last_assistant = compat_messages[-1]
                    tool_calls = last_assistant.setdefault("toolCalls", [])
                    for tool_result in tool_results:
                        matched = None
                        for tool_call in tool_calls:
                            if str(tool_call.get("id", "")) == tool_result.tool_use_id:
                                matched = tool_call
                                break
                        if matched is None:
                            matched = {
                                "id": tool_result.tool_use_id,
                                "name": str(
                                    tool_result.metadata.get("tool_name", "")
                                    or tool_result.metadata.get("name", "")
                                    or "unknown"
                                ),
                                "input": {},
                            }
                            tool_calls.append(matched)
                        matched["status"] = "error" if tool_result.is_error else "done"
                        matched["result"] = tool_result_content_to_text(tool_result.content)

                user_text = self._collect_message_text(message)
                if user_text.startswith("# Companion\n") or user_text.startswith("[Context attachments]"):
                    continue
                if user_text:
                    compat_messages.append(
                        {
                            "role": "user",
                            "content": user_text,
                            "created_at": self._iso_timestamp(message.metadata.get("timestamp", time.time())),
                            "id": message.metadata.get("uuid", ""),
                        }
                    )
                continue

            assistant_text = self._collect_message_text(message)
            compat_message: dict[str, Any] = {
                "role": "assistant",
                "content": assistant_text,
                "created_at": self._iso_timestamp(message.metadata.get("timestamp", time.time())),
                "id": message.metadata.get("uuid", ""),
            }
            tool_calls: list[dict[str, Any]] = []
            for block in message.tool_use_blocks:
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": dict(block.input),
                        "status": "running",
                    }
                )
            if tool_calls:
                compat_message["toolCalls"] = tool_calls
            compat_messages.append(compat_message)
        return compat_messages

    def _get_session_messages(self, session_id: str) -> list[Message]:
        state = self._sessions.get(session_id)
        if state is not None:
            store = getattr(state.agent, "_session_store", None)
            if store is not None:
                return store.load_session(session_id)
        return self._session_store.load_session(session_id)

    def list_compat_conversations(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for info in self._session_store.list_sessions():
            meta = self._load_session_metadata(info.session_id)
            items.append(
                {
                    "id": info.session_id,
                    "title": meta.title or info.title or "New Chat",
                    "updated_at": self._iso_timestamp(meta.updated_at or info.updated_at),
                    "created_at": self._iso_timestamp(meta.created_at),
                    "model": meta.model,
                    "research_mode": bool(meta.research_mode),
                    "project_id": meta.project_id or None,
                    "message_count": info.message_count,
                    "preview": info.preview,
                    "_sort_ts": float(meta.updated_at or 0),
                }
            )
            seen.add(info.session_id)

        for session in self._api.list_sessions():
            session_id = str(session.get("session_id", "") or "").strip()
            if not session_id or session_id in seen:
                continue
            meta = self._load_session_metadata(session_id)
            items.append(
                {
                    "id": session_id,
                    "title": meta.title or "New Chat",
                    "updated_at": self._iso_timestamp(meta.updated_at),
                    "created_at": self._iso_timestamp(meta.created_at),
                    "model": meta.model,
                    "research_mode": bool(meta.research_mode),
                    "project_id": meta.project_id or None,
                    "message_count": meta.message_count,
                    "preview": "",
                    "_sort_ts": float(meta.updated_at or time.time()),
                }
            )

        items.sort(
            key=lambda item: float(item.get("_sort_ts", 0) or 0),
            reverse=True,
        )
        for item in items:
            item.pop("_sort_ts", None)
        return items

    async def create_compat_conversation(
        self,
        *,
        title: str = "",
        model: str = "",
        research_mode: bool = False,
    ) -> dict[str, Any]:
        handle = await self.create_session({"source": "donor-frontend"})
        state = self._sessions.get(handle.session_id)
        if state is not None:
            if title:
                setattr(state.agent, "_session_name", title)
            state.research_mode = bool(research_mode)
            if model:
                with contextlib.suppress(Exception):
                    state.agent.provider._config.model = model  # type: ignore[attr-defined]
        meta = self._load_session_metadata(handle.session_id)
        meta.title = title or meta.title or "New Chat"
        if model:
            meta.model = model
        elif state is not None:
            meta.model = str(getattr(state.agent.provider, "model_name", "") or meta.model)
        meta.research_mode = bool(research_mode)
        self._save_session_metadata(meta)
        return {
            "id": handle.session_id,
            "title": meta.title,
            "model": meta.model,
            "research_mode": meta.research_mode,
            "created_at": self._iso_timestamp(meta.created_at),
            "updated_at": self._iso_timestamp(meta.updated_at),
        }

    def get_compat_conversation(self, session_id: str) -> dict[str, Any] | None:
        meta = self._load_session_metadata(session_id)
        exists = self._session_store.session_exists(session_id) or session_id in self._sessions
        if not exists:
            return None
        state = self._sessions.get(session_id)
        if state is not None:
            meta = self._sync_session_metadata_from_state(session_id, state)
        messages = self._convert_messages_to_compat(self._get_session_messages(session_id))
        return {
            "id": session_id,
            "title": meta.title or "New Chat",
            "model": meta.model,
            "research_mode": bool(meta.research_mode),
            "project_id": meta.project_id or None,
            "messages": messages,
            "created_at": self._iso_timestamp(meta.created_at),
            "updated_at": self._iso_timestamp(meta.updated_at),
        }

    def update_compat_conversation(self, session_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        conversation = self.get_compat_conversation(session_id)
        if conversation is None:
            return None
        state = self._sessions.get(session_id)
        meta = self._load_session_metadata(session_id)
        if "title" in data:
            title = str(data.get("title", "") or "").strip()
            meta.title = title
            if state is not None:
                setattr(state.agent, "_session_name", title)
        if "research_mode" in data:
            meta.research_mode = bool(data.get("research_mode"))
            if state is not None:
                state.research_mode = meta.research_mode
        if "project_id" in data:
            meta.project_id = str(data.get("project_id", "") or "").strip()
            if state is not None:
                state.project_id = meta.project_id
        if "model" in data:
            model = str(data.get("model", "") or "").strip()
            if model:
                meta.model = model
                if state is not None:
                    with contextlib.suppress(Exception):
                        state.agent.provider._config.model = model  # type: ignore[attr-defined]
        self._save_session_metadata(meta)
        return self.get_compat_conversation(session_id)

    async def delete_compat_conversation(self, session_id: str) -> bool:
        if session_id in self._sessions:
            await self._shutdown_session_async(session_id)
        else:
            self._api.remove_session(session_id)
        self._session_store.delete_session(session_id)
        return True

    async def stop_compat_generation(self, session_id: str) -> dict[str, Any]:
        state = self._sessions.get(session_id)
        if state is None or state.active_query is None or state.active_query.done():
            return {"ok": True, "status": "idle"}
        with contextlib.suppress(Exception):
            state.agent.cancel_submit()
        state.active_query.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state.active_query
        state.active_query = None
        return {"ok": True, "status": "stopped"}

    def _stream_events_since(self, session_id: str, since: int) -> list[dict[str, Any]]:
        events = self._api.get_events(session_id, since=since, limit=1000) or []
        return [
            event for event in events
            if str(event.get("type", "")) == "stream_event"
        ]

    def get_compat_stream_status(self, session_id: str) -> dict[str, Any]:
        state = self._sessions.get(session_id)
        if state is None:
            return {"active": False, "eventCount": 0}
        events = self._stream_events_since(session_id, state.active_stream_since)
        return {"active": bool(state.active_query and not state.active_query.done()), "eventCount": len(events)}

    def get_compat_generation_status(self, session_id: str) -> dict[str, Any]:
        state = self._sessions.get(session_id)
        if state is None:
            return {"active": False, "status": "idle", "text": "", "thinking": ""}

        text_parts: list[str] = []
        thinking_parts: list[str] = []
        thinking_summary = ""
        for event in self._stream_events_since(session_id, state.active_stream_since):
            payload = event.get("payload", {})
            event_type = str(payload.get("event_type", "") or "")
            if event_type in {"text", "assistant_delta", "text_delta"}:
                chunk = str(payload.get("text", "") or "")
                if chunk:
                    text_parts.append(chunk)
            elif event_type == "thinking":
                chunk = str(payload.get("text", "") or "")
                if chunk:
                    thinking_parts.append(chunk)
            elif event_type == "thinking_summary":
                thinking_summary = str(payload.get("text", "") or payload.get("summary", "") or "")
            elif event_type == "completion":
                chunk = str(payload.get("text", "") or "")
                if chunk:
                    text_parts = [chunk]
            elif event_type == "executor_error":
                return {
                    "active": False,
                    "status": "error",
                    "text": "".join(text_parts),
                    "thinking": "".join(thinking_parts),
                    "thinkingSummary": thinking_summary,
                    "error": str(payload.get("error", "") or ""),
                }

        return {
            "active": bool(state.active_query and not state.active_query.done()),
            "status": "generating" if state.active_query and not state.active_query.done() else "idle",
            "text": "".join(text_parts),
            "thinking": "".join(thinking_parts),
            "thinkingSummary": thinking_summary,
            "crossProcess": False,
        }

    def get_compat_context_size(self, session_id: str) -> dict[str, int]:
        meta = self._load_session_metadata(session_id)
        cfg = load_config()
        limit = int(getattr(cfg, "compact_threshold", 160000) or 160000) + 33000
        return {"tokens": int(meta.total_tokens or 0), "limit": limit}

    async def start_compat_chat(
        self,
        *,
        session_id: str,
        message: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        state = await self._ensure_started(session_id)
        status = self._api.get_session_status(session_id)
        state.active_stream_since = max(0, int(status.get("next_sequence_num", 1) or 1) - 1)
        result = await self._handle_query(
            session_id,
            message,
            metadata={"source": "donor-frontend"},
            attachments=attachments,
        )
        return {
            "status": result,
            "stream_since": state.active_stream_since,
        }

    async def answer_compat_question(
        self,
        *,
        session_id: str,
        request_id: str,
        tool_use_id: str,
        answers: dict[str, Any],
    ) -> dict[str, Any]:
        del tool_use_id
        normalized = " ".join(str(value or "") for value in answers.values()).strip().lower()
        allow = normalized in {"y", "yes", "allow", "ok", "true", "1", "继续", "允许", "同意"}
        result = await self._handle_control_response(
            session_id,
            request_id,
            {
                "id": request_id,
                "decision": "allow" if allow else "deny",
                "allow": allow,
            },
        )
        return {"ok": result == "accepted"}

    def _ensure_session_state(self, session_id: str) -> _ExecutorSessionState:
        state = self._sessions.get(session_id)
        if state is None:
            state = _ExecutorSessionState(
                agent=self._agent_factory(session_id),
            )
            self._install_remote_permission_runtime(session_id, state)
            async def _forward_runtime_event(event: Any) -> None:
                await self._publish_stream_event(
                    session_id,
                    _serialize_stream_event(event),
                )

            register = getattr(state.agent, "on_event", None)
            if callable(register):
                state.unsubscribe = register(_forward_runtime_event)
            self._sessions[session_id] = state
        return state

    @staticmethod
    def _resolve_tool_path(
        working_directory: str,
        tool_input: dict[str, Any],
    ) -> Path | None:
        raw_value = str(
            tool_input.get("file_path", "") or tool_input.get("path", "") or ""
        ).strip()
        if not raw_value or any(char in raw_value for char in "*?[]"):
            return None
        try:
            candidate = Path(raw_value).expanduser()
            if not candidate.is_absolute():
                candidate = Path(working_directory or ".").expanduser() / candidate
            return candidate.resolve()
        except OSError:
            return None

    @staticmethod
    def _infer_operation_type(tool_name: str, *, is_read_only: bool) -> str:
        if is_read_only:
            return "read"
        if tool_name.lower() in {"write", "filewrite"}:
            return "create"
        return "write"

    @staticmethod
    def _build_directory_allow_rules(directory: str) -> list[PermissionRule]:
        pattern = str(Path(directory).expanduser().resolve() / "*")
        tool_names = ["Read", "Edit", "Write", "NotebookEdit", "FileEdit", "FileWrite"]
        return [
            PermissionRule(
                tool_pattern=f"{tool_name}({pattern})",
                decision=PermissionDecision.ALLOW,
                reason="session_directory_scope",
            )
            for tool_name in tool_names
        ]

    def _install_remote_permission_runtime(
        self,
        session_id: str,
        state: _ExecutorSessionState,
    ) -> None:
        agent = state.agent
        try:
            cfg = load_config()
            classifier_provider = (
                agent.provider
                if cfg.permission_mode == PermissionMode.AUTO.value
                else None
            )

            async def _ask_callback(
                tool_name: str,
                tool_input: dict[str, Any],
            ) -> PermissionDecision:
                tool = find_tool_by_name(agent.tools, tool_name)
                is_read_only = bool(getattr(tool, "is_read_only", False))
                resolved_path = self._resolve_tool_path(agent.working_directory, tool_input)
                directory_path = ""
                if resolved_path is not None:
                    directory_path = str(
                        resolved_path if resolved_path.is_dir() else resolved_path.parent
                    )
                operation_type = self._infer_operation_type(
                    tool_name,
                    is_read_only=is_read_only,
                )
                request_id = uuid4().hex[:12]
                loop = asyncio.get_running_loop()
                future: asyncio.Future[dict[str, Any]] = loop.create_future()
                state.pending_control_requests[request_id] = future
                await self._publish_stream_event(
                    session_id,
                    {
                        "event_type": "control_request",
                        "request_id": request_id,
                        "request_type": "can_use_tool",
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "permission_mode": cfg.permission_mode,
                        "operation_type": operation_type,
                        "working_directory": agent.working_directory,
                        "file_path": str(resolved_path) if resolved_path is not None else "",
                        "directory_path": directory_path,
                        "reference_directories": list(agent.reference_directories),
                    },
                )
                try:
                    response = await asyncio.wait_for(future, timeout=300.0)
                except asyncio.TimeoutError:
                    state.pending_control_requests.pop(request_id, None)
                    await self._publish_stream_event(
                        session_id,
                        {
                            "event_type": "control_request_resolved",
                            "request_id": request_id,
                            "decision": PermissionDecision.DENY.value,
                            "reason": "timeout",
                        },
                    )
                    return PermissionDecision.DENY

                state.pending_control_requests.pop(request_id, None)
                decision_raw = str(
                    response.get("decision", "")
                    or ("allow" if response.get("allow") else "deny"),
                ).strip().lower()
                try:
                    decision = PermissionDecision(decision_raw)
                except Exception:
                    decision = PermissionDecision.DENY

                granted_scope = str(response.get("scope", "") or "").strip().lower()
                granted_directory = str(
                    response.get("scope_path", "") or response.get("directory_path", "") or ""
                ).strip()
                checker = getattr(agent, "_permission_checker", None)
                if (
                    decision == PermissionDecision.ALLOW
                    and granted_scope == "directory"
                    and granted_directory
                    and checker is not None
                ):
                    normalized_directory = str(Path(granted_directory).expanduser().resolve())
                    checker.add_additional_directories([normalized_directory])
                    if operation_type != "read":
                        checker.add_rules(
                            self._build_directory_allow_rules(normalized_directory),
                        )
                    agent.add_reference_directory(normalized_directory)

                await self._publish_stream_event(
                    session_id,
                    {
                        "event_type": "control_request_resolved",
                        "request_id": request_id,
                        "decision": decision.value,
                        "scope": granted_scope,
                        "scope_path": granted_directory,
                    },
                )
                return decision

            agent._permission_checker = build_permission_checker(
                mode=cfg.permission_mode,
                raw_rules=cfg.permission_rules,
                classifier_provider=classifier_provider,
                project_dir=agent.working_directory or "",
                additional_dirs=agent.reference_directories,
                ask_callback=_ask_callback,
            )
        except Exception:
            agent._permission_checker = None

    def _get_runtime_snapshot(self, session_id: str) -> dict[str, Any]:
        state = self._sessions.get(session_id)
        if state is None:
            return {}

        agent = state.agent
        background_runner = getattr(agent, "background_runner", None)
        background_tasks = []
        if background_runner is not None:
            list_task_snapshots = getattr(background_runner, "list_task_snapshots", None)
            if callable(list_task_snapshots):
                try:
                    background_tasks = list_task_snapshots(include_completed=True)
                except Exception:
                    background_tasks = []

        team_snapshot = self._build_team_snapshot(agent)
        return {
            "taskListId": self._resolve_task_list_id(session_id, agent),
            "backgroundTasks": background_tasks,
            "team": team_snapshot,
            "planState": self._build_plan_state_snapshot(agent),
            "workingDirectory": str(getattr(agent, "working_directory", "") or ""),
            "referenceDirectories": list(getattr(agent, "reference_directories", []) or []),
        }

    @staticmethod
    def _resolve_task_list_id(session_id: str, agent: Agent) -> str:
        team_tool = getattr(agent, "_team_create_tool", None)
        team_name = str(getattr(team_tool, "_active_team_name", "") or "").strip()
        return team_name or session_id

    @staticmethod
    def _build_plan_state_snapshot(agent: Agent) -> dict[str, Any]:
        """Derive plan-mode state from this session's transcript, not globals."""
        try:
            messages = list(agent.messages)
        except Exception:
            return {}

        tool_names_by_id: dict[str, str] = {}
        latest_enter_index = -1
        latest_exit_index = -1
        latest_plan_text = ""

        for index, message in enumerate(messages):
            for tool_use in getattr(message, "tool_use_blocks", []):
                tool_use_id = str(getattr(tool_use, "id", "") or "").strip()
                tool_name = str(getattr(tool_use, "name", "") or "").strip().lower()
                if tool_use_id:
                    tool_names_by_id[tool_use_id] = tool_name
                if tool_name == "enterplanmode":
                    latest_enter_index = index
                elif tool_name == "exitplanmode":
                    latest_exit_index = index

            for tool_result in getattr(message, "tool_result_blocks", []):
                if bool(getattr(tool_result, "is_error", False)):
                    continue
                tool_use_id = str(getattr(tool_result, "tool_use_id", "") or "").strip()
                if tool_names_by_id.get(tool_use_id) != "exitplanmode":
                    continue
                raw = tool_result_content_to_text(getattr(tool_result, "content", "")).strip()
                if not raw:
                    continue
                latest_exit_index = index
                marker = "## Implementation Plan"
                latest_plan_text = raw[raw.index(marker):].strip() if marker in raw else raw

        if latest_enter_index > latest_exit_index:
            return {
                "isActive": True,
                "planText": "",
                "prePermissionMode": "plan",
            }

        if latest_exit_index >= 0 and latest_exit_index >= latest_enter_index and latest_plan_text:
            return {
                "isActive": False,
                "planText": latest_plan_text,
                "prePermissionMode": "default",
            }

        return {}

    def _build_team_snapshot(self, agent: Agent) -> dict[str, Any]:
        from ..delegation.team_files import TEAM_LEAD_NAME, read_team_file

        team_tool = getattr(agent, "_team_create_tool", None)
        team_name = str(getattr(team_tool, "_active_team_name", "") or "").strip()
        if not team_name:
            return {}

        persisted = read_team_file(team_name) or {}
        live_members: dict[str, dict[str, Any]] = {}
        if team_tool is not None:
            team = team_tool.get_team(team_name)
            if team is not None:
                try:
                    for state in team.list_teammates():
                        live_members[state.identity.agent_id] = {
                            "agentId": state.identity.agent_id,
                            "name": state.identity.agent_name,
                            "teamName": state.identity.team_name,
                            "color": state.identity.color or "",
                            "planModeRequired": bool(state.identity.plan_mode_required),
                            "status": getattr(state.status, "value", str(state.status)),
                            "currentTask": state.current_task,
                            "messagesProcessed": int(state.messages_processed),
                            "totalTurns": int(state.total_turns),
                            "error": state.error,
                            "isIdle": bool(state.is_idle),
                            "lastUpdateMs": int(getattr(state, "last_update_ms", 0) or 0),
                            "transcriptFile": str(getattr(state, "transcript_file", "") or ""),
                            "isActive": getattr(state.status, "value", str(state.status)) != "shutdown",
                            "backendType": "in-process",
                        }
                except Exception:
                    live_members = {}

        members_payload: list[dict[str, Any]] = []
        raw_members = persisted.get("members", [])
        if isinstance(raw_members, list):
            for member in raw_members:
                if not isinstance(member, dict):
                    continue
                agent_id = str(member.get("agentId", "") or "").strip()
                merged = dict(member)
                if agent_id and agent_id in live_members:
                    merged.update(live_members[agent_id])
                merged.setdefault("agentId", agent_id)
                merged.setdefault("name", str(member.get("name", "") or ""))
                merged.setdefault("status", "idle" if merged.get("isActive") else "shutdown")
                members_payload.append(merged)

        seen_ids = {str(member.get("agentId", "") or "") for member in members_payload}
        for agent_id, member in live_members.items():
            if agent_id not in seen_ids:
                members_payload.append(member)

        active_count = sum(1 for member in members_payload if bool(member.get("isActive", False)))
        teammate_count = sum(
            1 for member in members_payload if str(member.get("name", "") or "") != TEAM_LEAD_NAME
        )
        return {
            "name": str(persisted.get("name", team_name) or team_name),
            "description": str(persisted.get("description", "") or ""),
            "leadAgentId": str(persisted.get("leadAgentId", "") or ""),
            "leadSessionId": str(persisted.get("leadSessionId", "") or ""),
            "members": members_payload,
            "activeCount": active_count,
            "teammateCount": teammate_count,
        }

    async def _control_runtime_task(
        self,
        session_id: str,
        *,
        task_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = self._sessions.get(session_id)
        if state is None:
            return {"ok": False, "error": f"Unknown session: {session_id}"}

        agent = state.agent
        action_name = action.strip().lower()
        extra = dict(payload or {})
        message_text = str(extra.get("message", "") or "").strip()

        if action_name == "reset_task_list_if_completed":
            return self._reset_completed_task_list(session_id, agent)

        task = agent.get_task(task_id)
        if task is not None:
            if action_name == "stop":
                stopped = agent.cancel_task(task_id)
                return {"ok": stopped, "action": action_name, "task_id": task_id}
            if action_name in {"resume", "send_message"}:
                if not message_text:
                    return {"ok": False, "error": "message is required for resume/send_message"}
                accepted = agent.send_message_to_task(task_id, message_text)
                return {"ok": accepted, "action": action_name, "task_id": task_id}
            if action_name in {"foreground", "describe"}:
                return {
                    "ok": True,
                    "action": action_name,
                    "task_id": task_id,
                    "task": self._task_info_to_payload(task),
                }

        team_tool = getattr(agent, "_team_create_tool", None)
        team_name = str(getattr(team_tool, "_active_team_name", "") or "").strip()
        team = team_tool.get_team(team_name) if team_tool is not None and team_name else None
        if team is not None:
            member = None
            for candidate in self._build_team_snapshot(agent).get("members", []):
                candidate_id = str(candidate.get("agentId", "") or "")
                candidate_name = str(candidate.get("name", "") or "")
                if candidate_id == task_id or candidate_name == task_id:
                    member = candidate
                    break
            if action_name == "stop":
                if member is None:
                    return {"ok": False, "error": f"Unknown teammate: {task_id}"}
                await team.shutdown_teammate(task_id)
                return {"ok": True, "action": action_name, "task_id": task_id}
            if action_name in {"resume", "send_message"}:
                if not message_text:
                    return {"ok": False, "error": "message is required for resume/send_message"}
                if member is None:
                    return {"ok": False, "error": f"Unknown teammate: {task_id}"}
                accepted = bool(team.send_message(task_id, message_text))
                if not accepted:
                    return {"ok": False, "error": f"Unknown teammate: {task_id}"}
                return {"ok": True, "action": action_name, "task_id": task_id}
            if action_name in {"foreground", "describe"}:
                if member is not None:
                    return {"ok": True, "action": action_name, "task_id": task_id, "member": member}

        return {"ok": False, "error": f"Unknown task or teammate: {task_id}"}

    @staticmethod
    def _reset_completed_task_list(session_id: str, agent: Agent) -> dict[str, Any]:
        from ..tools.task_tools import TaskBoard

        task_list_id = RemoteExecutorHost._resolve_task_list_id(session_id, agent)
        board = TaskBoard()
        board.set_scope(task_list_id)
        tasks = [task for task in board.list() if not task.metadata.get("_internal")]
        if not tasks:
            return {
                "ok": True,
                "cleared": False,
                "task_list_id": task_list_id,
                "reason": "empty",
            }
        if any(str(task.status) != "completed" for task in tasks):
            return {
                "ok": True,
                "cleared": False,
                "task_list_id": task_list_id,
                "reason": "incomplete",
            }

        cleared = board.reset()
        return {
            "ok": True,
            "cleared": cleared,
            "task_list_id": task_list_id,
        }

    def _get_runtime_transcript(
        self,
        session_id: str,
        *,
        task_id: str,
        limit: int = 200,
    ) -> dict[str, Any]:
        state = self._sessions.get(session_id)
        if state is None:
            return {"ok": False, "error": f"Unknown session: {session_id}"}

        agent = state.agent
        transcript_file = ""
        task = agent.get_task(task_id)
        if task is not None:
            transcript_file = str(getattr(task, "transcript_file", "") or "")
        if not transcript_file:
            for member in self._build_team_snapshot(agent).get("members", []):
                if str(member.get("agentId", "") or "") == task_id or str(member.get("name", "") or "") == task_id:
                    transcript_file = str(member.get("transcriptFile", "") or "")
                    break
        if not transcript_file:
            return {"ok": False, "error": f"No transcript found for {task_id}"}

        path = Path(transcript_file)
        if not path.exists():
            return {"ok": False, "error": f"Transcript file not found: {transcript_file}"}

        lines = path.read_text(encoding="utf-8").splitlines()
        entries: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                parsed = json.loads(line)
            except Exception:
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
        return {
            "ok": True,
            "task_id": task_id,
            "transcript_file": transcript_file,
            "entries": entries,
        }

    @staticmethod
    def _task_info_to_payload(task: Any) -> dict[str, Any]:
        metadata = dict(getattr(task, "metadata", {}) or {})
        return {
            "id": str(getattr(task, "id", "") or ""),
            "status": str(getattr(getattr(task, "status", None), "value", getattr(task, "status", "")) or ""),
            "description": str(getattr(task, "description", "") or ""),
            "type": str(getattr(getattr(task, "type", None), "value", getattr(task, "type", "")) or ""),
            "outputFile": str(getattr(task, "output_file", "") or ""),
            "transcriptFile": str(getattr(task, "transcript_file", "") or ""),
            "metadata": metadata,
        }

    async def _publish_stream_event(
        self,
        session_id: str,
        payload: dict[str, Any],
    ) -> None:
        timestamp = time.time()
        self._api.append_event(
            session_id,
            "stream_event",
            payload,
            timestamp=timestamp,
        )
        await self._server.push_event(
            session_id,
            {
                "type": "stream_event",
                "payload": payload,
                "timestamp": timestamp,
            },
        )
        await self._webrtc.push_event(
            session_id,
            {
                "type": "stream_event",
                "payload": payload,
                "timestamp": timestamp,
            },
        )

    async def _ensure_started(self, session_id: str) -> _ExecutorSessionState:
        state = self._ensure_session_state(session_id)
        if not state.started:
            await state.agent.start()
            state.started = True
        return state

    async def _handle_query(
        self,
        session_id: str,
        query_text: str,
        *,
        metadata: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> str:
        state = await self._ensure_started(session_id)
        if state.active_query is not None and not state.active_query.done():
            return "busy"
        session_status = self._api.get_session_status(session_id)
        state.active_stream_since = max(
            0,
            int(session_status.get("next_sequence_num", 1) or 1) - 1,
        )

        query_metadata = dict(metadata or {})
        session_metadata = session_status.get("metadata", {}) if isinstance(session_status, dict) else {}
        if isinstance(session_metadata, dict):
            source = str(session_metadata.get("source", "") or "").strip()
            if source:
                query_metadata.setdefault("source", source)
            if source and source != "ccmini-frontend":
                query_metadata.setdefault("bridge_origin", True)
                query_metadata.setdefault("skip_slash_commands", True)

        state.active_query = asyncio.create_task(
            self._run_query(
                session_id,
                state,
                query_text,
                query_metadata,
                list(attachments or []),
            ),
            name=f"remote-executor-{session_id}",
        )
        return "accepted"

    async def _handle_submit_tool_results(
        self,
        session_id: str,
        run_id: str,
        results_payload: list[dict[str, Any]],
    ) -> str:
        state = await self._ensure_started(session_id)
        if state.active_query is not None and not state.active_query.done():
            return "busy"
        session_status = self._api.get_session_status(session_id)
        state.active_stream_since = max(
            0,
            int(session_status.get("next_sequence_num", 1) or 1) - 1,
        )

        state.active_query = asyncio.create_task(
            self._run_submit_tool_results(session_id, state, run_id, results_payload),
            name=f"remote-executor-submit-{session_id}",
        )
        return "accepted"

    async def _handle_control_response(
        self,
        session_id: str,
        response_id: str,
        payload: dict[str, Any],
    ) -> str:
        state = self._sessions.get(session_id)
        if state is None:
            raise RuntimeError(f"Unknown session: {session_id}")
        future = state.pending_control_requests.get(response_id)
        if future is None:
            return "missing"
        if not future.done():
            future.set_result(dict(payload))
        return "accepted"

    async def _handle_tool_call(
        self,
        session_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> str:
        state = await self._ensure_started(session_id)
        if state.active_query is not None and not state.active_query.done():
            return "busy"

        async with state.lock:
            tool = find_tool_by_name(state.agent.tools, tool_name)
            if tool is None:
                raise RuntimeError(f"Unknown tool: {tool_name}")
            if isinstance(tool, ClientTool):
                raise RuntimeError(
                    f"Tool {tool_name} requires client-side execution and cannot run over RemoteTrigger."
                )

            context = self._build_tool_context(session_id, state.agent)
            raw_result = await tool.execute(
                context=context,
                **(tool_input if isinstance(tool_input, dict) else {}),
            )
            if isinstance(raw_result, ToolResult):
                output_text = self._stringify_tool_output(raw_result.output)
                if raw_result.context_modifier is not None:
                    context = raw_result.context_modifier(context)
                if raw_result.behavior in {"error", "deny"}:
                    raise RuntimeError(output_text or f"{tool_name} returned {raw_result.behavior}")
                return output_text
            return self._stringify_tool_output(raw_result)

    def _build_tool_context(self, session_id: str, agent: Agent) -> ToolUseContext:
        system_prompt = ""
        renderer = getattr(agent, "_system_prompt", None)
        if renderer is not None and hasattr(renderer, "render"):
            try:
                system_prompt = renderer.render()
            except Exception:
                system_prompt = ""
        return ToolUseContext(
            conversation_id=session_id,
            agent_id=agent._agent_id,
            messages=list(agent.messages),
            system_prompt=system_prompt,
            options={"tools": list(agent.tools)},
            extras={
                "agent": agent,
                "messages": list(agent.messages),
                "system_prompt": system_prompt,
                "attachment_collector": getattr(agent, "_attachment_collector", None),
                "summary_provider": getattr(agent, "_summary_provider", None),
                "fallback_config": getattr(agent, "_fallback_config", None),
                "session_memory_content": agent._get_session_memory_content(),
                "working_directory": agent.working_directory,
                "query_source": "remote_trigger",
            },
        )

    @staticmethod
    def _stringify_tool_output(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return tool_result_content_to_text(value)
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)

    async def _run_query(
        self,
        session_id: str,
        state: _ExecutorSessionState,
        query_text: str,
        metadata: dict[str, Any] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        async with state.lock:
            try:
                async for event in state.agent.query(
                    query_text,
                    conversation_id=session_id,
                    metadata=metadata,
                    attachments=attachments,
                ):
                    await self._publish_stream_event(
                        session_id,
                        _serialize_stream_event(event),
                    )
            except Exception as exc:
                await self._publish_stream_event(
                    session_id,
                    {
                        "event_type": "executor_error",
                        "error": str(exc),
                    },
                )
            finally:
                state.active_query = None

    async def _run_submit_tool_results(
        self,
        session_id: str,
        state: _ExecutorSessionState,
        run_id: str,
        results: list[dict[str, Any]],
    ) -> None:
        async with state.lock:
            try:
                async for event in state.agent.submit_tool_results(run_id, results):
                    await self._publish_stream_event(
                        session_id,
                        _serialize_stream_event(event),
                    )
            except Exception as exc:
                await self._publish_stream_event(
                    session_id,
                    {
                        "event_type": "executor_error",
                        "error": str(exc),
                    },
                )
            finally:
                state.active_query = None

    def _shutdown_session(self, session_id: str) -> None:
        state = self._sessions.pop(session_id, None)
        if state is None:
            return
        for future in state.pending_control_requests.values():
            if not future.done():
                future.cancel()
        state.pending_control_requests.clear()
        if state.unsubscribe is not None:
            with contextlib.suppress(Exception):
                state.unsubscribe()
        if state.active_query is not None and not state.active_query.done():
            state.active_query.cancel()
        asyncio.create_task(self._finalize_agent_stop(state))

    async def _shutdown_session_async(self, session_id: str) -> None:
        state = self._sessions.pop(session_id, None)
        if state is None:
            return
        for future in state.pending_control_requests.values():
            if not future.done():
                future.cancel()
        state.pending_control_requests.clear()
        if state.unsubscribe is not None:
            with contextlib.suppress(Exception):
                state.unsubscribe()
        if state.active_query is not None and not state.active_query.done():
            state.active_query.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await state.active_query
        await self._finalize_agent_stop(state)

    async def _finalize_agent_stop(self, state: _ExecutorSessionState) -> None:
        if state.started:
            await state.agent.stop()


def create_remote_executor_host(
    *,
    provider: ProviderConfig | BaseProvider,
    system_prompt: str | SystemPrompt,
    profile: RuntimeProfile | str = RuntimeProfile.ROBOT_BRAIN,
    bridge_config: BridgeConfig | None = None,
    tools: list[Tool] | None = None,
    config: AgentConfig | None = None,
) -> RemoteExecutorHost:
    """Convenience helper to expose ccmini as a remote executor service."""

    from ..factory import create_agent

    def _factory(conversation_id: str) -> Agent:
        return create_agent(
            provider=provider,
            system_prompt=system_prompt,
            profile=profile,
            tools=tools,
            config=config,
            conversation_id=conversation_id,
            agent_id=f"remote-executor-{conversation_id}",
        )

    return RemoteExecutorHost(
        agent_factory=_factory,
        bridge_config=bridge_config or _load_default_bridge_config(),
    )
