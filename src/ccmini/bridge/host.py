"""Remote executor host for exposing ccmini Agent instances over the bridge."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..agent import Agent, AgentConfig
from ..messages import (
    CompletionEvent,
    ErrorEvent,
    PendingToolCallEvent,
    PromptSuggestionEvent,
    RequestStartEvent,
    SpeculationEvent,
    ThinkingEvent,
    TextEvent,
    ToolCallEvent,
    ToolProgressEvent,
    ToolResultEvent,
    ToolUseSummaryEvent,
    UsageEvent,
)
from ..profiles import RuntimeProfile
from ..prompts import SystemPrompt
from ..providers import BaseProvider, ProviderConfig
from ..tool import Tool
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
        return {"event_type": "text", "text": event.text, **base}
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
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    unsubscribe: Callable[[], None] | None = None


class _ExecutorBridgeAPI(BridgeAPI):
    def __init__(self, host: "RemoteExecutorHost") -> None:
        self._host = host
        super().__init__(
            on_query=self._host._handle_query,
            on_submit_tool_results=self._host._handle_submit_tool_results,
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
        self._api = _ExecutorBridgeAPI(self)
        self._server = BridgeServer(bridge_config or BridgeConfig(enabled=True), api=self._api)
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

    def _ensure_session_state(self, session_id: str) -> _ExecutorSessionState:
        state = self._sessions.get(session_id)
        if state is None:
            state = _ExecutorSessionState(
                agent=self._agent_factory(session_id),
            )
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

    async def _handle_query(self, session_id: str, query_text: str) -> str:
        state = await self._ensure_started(session_id)
        if state.active_query is not None and not state.active_query.done():
            return "busy"

        state.active_query = asyncio.create_task(
            self._run_query(session_id, state, query_text),
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

        state.active_query = asyncio.create_task(
            self._run_submit_tool_results(session_id, state, run_id, results_payload),
            name=f"remote-executor-submit-{session_id}",
        )
        return "accepted"

    async def _run_query(
        self,
        session_id: str,
        state: _ExecutorSessionState,
        query_text: str,
    ) -> None:
        async with state.lock:
            try:
                async for event in state.agent.query(
                    query_text,
                    conversation_id=session_id,
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
