"""Tests for the v4 websocket app bound to a RuntimeSession."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reachy_mini.action_runtime import ActionExecutor, ActionResult, ActionSpec
from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.pipeline.action_dispatcher import ActionDispatcher
from reachy_mini.pipeline.session import RuntimeSession
from reachy_mini.pipeline.speech_presenter import SpeechPresenter
from reachy_mini.pipeline.tts_kokoro import KokoroAdapter
from reachy_mini.pipeline.ws_app import build_ws_app
from reachy_mini.reachy_brain.agent import BrainAgent
from reachy_mini.reachy_brain.config import (
    AgentConfig,
    ModelConfig,
    SpeechConfig,
    SpeechInputConfig,
    VisionConfig,
)
from reachy_mini.reachy_brain.coordinator import WorkerCoordinator


class _ScriptedModel:
    def __init__(self, scripted: list[dict[str, Any]]) -> None:
        self._scripted = list(scripted)

    async def run(self, turn_input, *, config, registry):
        if self._scripted:
            return self._scripted.pop(0)
        return {"reply_text": "ok", "speech_style": {}, "tool_calls": [], "worker_decision": None}


class _FakeMini:
    def goto_target(self, **_kwargs: Any) -> None:
        return None

    def look_at_world(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def get_present_antenna_joint_positions(self) -> list[float]:
        return [0.0, 0.0]


def _build_session(scripted: list[dict[str, Any]] | None = None) -> RuntimeSession:
    config = AgentConfig(
        model=ModelConfig(provider="mock", model="mock"),
        speech=SpeechConfig(enabled=False),
        speech_input=SpeechInputConfig(enabled=False),
        vision=VisionConfig(),
        extras={},
    )
    registry = create_builtin_registry()
    agent = BrainAgent(
        config=config,
        registry=registry,
        model=_ScriptedModel(scripted or []),
    )
    executor = ActionExecutor(registry=registry, mini=_FakeMini())

    async def emit_action(spec: ActionSpec) -> ActionResult:
        return await executor.submit(spec)

    coordinator = WorkerCoordinator(emit_action=emit_action)
    return RuntimeSession(
        config=config,
        registry=registry,
        agent=agent,
        executor=executor,
        speech=SpeechPresenter(),
        tts=KokoroAdapter(config.speech),
        actions=ActionDispatcher(executor),
        coordinator=coordinator,
    )


def _start_session(session: RuntimeSession) -> None:
    """Start a session on a background thread loop owned by FastAPI's TestClient.

    TestClient runs the app in its own loop, so we have to ensure the session
    has been started in that same loop before the websocket connects.
    """

    loop = asyncio.new_event_loop()
    started = threading.Event()

    def runner() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(session.start())
        started.set()
        loop.run_forever()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    started.wait(timeout=2.0)


@pytest.mark.asyncio
async def test_browser_input_round_trip_yields_brain_reply() -> None:
    """A text browser_input message produces a brain_reply via websocket."""
    session = _build_session(
        [
            {
                "reply_text": "hi",
                "speech_style": {},
                "tool_calls": [
                    {"name": "nod", "arguments": {"cycles": 1, "period_s": 0.3}, "reason": "ack"}
                ],
                "worker_decision": None,
            }
        ]
    )
    await session.start()
    app = build_ws_app(session)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/agent") as ws:
            ws.send_json(
                {
                    "type": "browser_input",
                    "ts_ms": 1,
                    "payload": {
                        "kind": "text",
                        "session_id": "S1",
                        "payload": {"text": "hi", "turn_id": "T1"},
                    },
                }
            )
            seen_brain = False
            seen_action_result = False
            for _ in range(20):
                envelope = ws.receive_json()
                if envelope["type"] == "brain_reply":
                    seen_brain = True
                    assert envelope["payload"]["reply_text"] == "hi"
                if envelope["type"] == "action_result":
                    seen_action_result = True
                    assert envelope["payload"]["status"] == "ok"
                    break
            assert seen_brain
            assert seen_action_result

    await session.stop()


@pytest.mark.asyncio
async def test_ping_returns_pong() -> None:
    """A ping inbound returns a pong outbound."""
    session = _build_session()
    await session.start()
    app = build_ws_app(session)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/agent") as ws:
            ws.send_json({"type": "ping", "payload": {}})
            for _ in range(10):
                envelope = ws.receive_json()
                if envelope["type"] == "pong":
                    break
            else:  # pragma: no cover
                pytest.fail("did not receive pong")

    await session.stop()


@pytest.mark.asyncio
async def test_legacy_protocol_emits_pipeline_error_and_closes() -> None:
    """Legacy front_* inbound types yield pipeline_error and close the socket."""
    session = _build_session()
    await session.start()
    app = build_ws_app(session)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/agent") as ws:
            ws.send_json({"type": "front_hint_chunk", "payload": {"text": "x"}})
            envelope = ws.receive_json()
            assert envelope["type"] == "pipeline_error"
            assert envelope["payload"]["reason"] == "legacy_protocol_rejected"
            with pytest.raises(Exception):
                ws.receive_json()

    await session.stop()
