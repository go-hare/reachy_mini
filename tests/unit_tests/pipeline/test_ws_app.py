"""Tests for the v4 websocket app bound to a RuntimeSession."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdk_fakes import (  # noqa: E402
    ScriptedSDKClient,
    agent_config,
    assistant_message,
    result_message,
)

from reachy_mini.action_runtime import ActionExecutor  # noqa: E402
from reachy_mini.action_runtime.library import create_builtin_registry  # noqa: E402
from reachy_mini.pipeline import ws_app as ws_app_module  # noqa: E402
from reachy_mini.pipeline.action_dispatcher import ActionDispatcher  # noqa: E402
from reachy_mini.pipeline.frames import CameraFrame  # noqa: E402
from reachy_mini.pipeline.session import RuntimeSession  # noqa: E402
from reachy_mini.pipeline.speech_presenter import SpeechPresenter  # noqa: E402
from reachy_mini.pipeline.tts_kokoro import KokoroAdapter  # noqa: E402
from reachy_mini.pipeline.ws_app import (  # noqa: E402
    _serve_websocket_with_handler,
    build_ws_app,
    run_ws_app,
)
from reachy_mini.reachy_brain.agent import BrainAgent  # noqa: E402


class _FakeMini:
    def goto_target(self, **_kwargs: Any) -> None:
        return None

    def look_at_world(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def get_present_antenna_joint_positions(self) -> list[float]:
        return [0.0, 0.0]


class _DelayedSDKClient(ScriptedSDKClient):
    """SDK fake that stays busy long enough for heartbeat traffic."""

    async def receive_response(self):
        """Yield a normal response after the server heartbeat has fired."""
        await asyncio.sleep(0.08)
        yield assistant_message("slow hi", session_id="T1")
        yield result_message(session_id="T1")


class _BlockingSDKClient(ScriptedSDKClient):
    """SDK fake that blocks until the test explicitly releases it."""

    gate: asyncio.Event | None = None

    async def receive_response(self):
        """Wait for the test gate before yielding a normal response."""
        assert self.gate is not None
        await self.gate.wait()
        yield assistant_message("unblocked", session_id="T1")
        yield result_message(session_id="T1")


def _build_session(
    scripts: list[list[Any]] | None = None,
    client_factory_override: Any | None = None,
) -> RuntimeSession:
    config = agent_config(speech_enabled=False)
    registry = create_builtin_registry()
    executor = ActionExecutor(registry=registry, mini=_FakeMini())
    actions = ActionDispatcher(executor)
    agent = BrainAgent(
        config=config,
        registry=registry,
        run_action=actions.run_action,
        client_factory=(
            client_factory_override
            if client_factory_override is not None
            else lambda options: ScriptedSDKClient(
                options,
                scripts=scripts or [],
                action_runner=actions.run_action,
            )
        ),
    )
    return RuntimeSession(
        config=config,
        registry=registry,
        agent=agent,
        executor=executor,
        speech=SpeechPresenter(),
        tts=KokoroAdapter(config.speech),
        actions=actions,
    )


@pytest.mark.asyncio
async def test_browser_input_round_trip_yields_sdk_message() -> None:
    """A text browser_input message produces sdk_message via websocket."""
    session = _build_session(
        [[assistant_message("hi", session_id="T1"), result_message(session_id="T1")]]
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
            seen_sdk = False
            for _ in range(20):
                envelope = ws.receive_json()
                if envelope["type"] == "sdk_message":
                    seen_sdk = True
                    assert envelope["payload"]["message_type"] == "AssistantMessage"
                    assert envelope["payload"]["content"][0]["text"] == "hi"
                    break
            assert seen_sdk

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
async def test_camera_frame_can_be_consumed_by_runtime_inbound_handler() -> None:
    """A custom app websocket handler can consume browser-fed camera frames."""
    session = _build_session()
    await session.start()
    captured: list[CameraFrame] = []

    async def handle_inbound(frame: Any) -> bool:
        if isinstance(frame, CameraFrame):
            captured.append(frame)
            return True
        return False

    app = build_ws_app(session)

    @app.websocket("/ws/agent-with-camera")
    async def runtime_socket(websocket: WebSocket) -> None:
        await _serve_websocket_with_handler(
            session,
            websocket,
            inbound_handler=handle_inbound,
        )

    with TestClient(app) as client:
        with client.websocket_connect("/ws/agent-with-camera") as ws:
            ws.send_json(
                {
                    "type": "camera_frame",
                    "ts_ms": 300,
                    "payload": {
                        "image_b64": "abcd",
                        "mime_type": "image/jpeg",
                        "width": 320,
                        "height": 180,
                    },
                }
            )
            for _ in range(20):
                if captured:
                    break
                await asyncio.sleep(0.01)

    assert len(captured) == 1
    assert captured[0].image_b64 == "abcd"
    assert captured[0].width == 320

    await session.stop()


@pytest.mark.asyncio
async def test_camera_frame_handler_is_not_blocked_by_slow_brain_turn() -> None:
    """camera_frame should not queue behind a slow browser_input turn."""
    gate = asyncio.Event()
    _BlockingSDKClient.gate = gate
    session = _build_session(
        client_factory_override=lambda options: _BlockingSDKClient(
            options,
            action_runner=lambda _spec: None,
        )
    )
    await session.start()
    captured: list[CameraFrame] = []

    async def handle_inbound(frame: Any) -> bool:
        if isinstance(frame, CameraFrame):
            captured.append(frame)
            return True
        return False

    app = build_ws_app(session)

    @app.websocket("/ws/agent-with-camera")
    async def runtime_socket(websocket: WebSocket) -> None:
        await _serve_websocket_with_handler(
            session,
            websocket,
            inbound_handler=handle_inbound,
        )

    try:
        with TestClient(app) as client:
            with client.websocket_connect("/ws/agent-with-camera") as ws:
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
                ws.send_json(
                    {
                        "type": "camera_frame",
                        "ts_ms": 300,
                        "payload": {
                            "image_b64": "abcd",
                            "mime_type": "image/jpeg",
                            "width": 320,
                            "height": 180,
                        },
                    }
                )
                for _ in range(20):
                    if captured:
                        break
                    await asyncio.sleep(0.01)
                assert captured
                gate.set()
    finally:
        _BlockingSDKClient.gate = None
        await session.stop()


@pytest.mark.asyncio
async def test_server_heartbeat_sends_ping_and_accepts_pong(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server heartbeat task sends ping and consumes browser pong."""
    monkeypatch.setattr(ws_app_module, "HEARTBEAT_INTERVAL_S", 0.01)
    monkeypatch.setattr(ws_app_module, "HEARTBEAT_TIMEOUT_S", 0.5)
    session = _build_session()
    await session.start()
    app = build_ws_app(session)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/agent") as ws:
            envelope = ws.receive_json()
            assert envelope == {"type": "ping", "payload": {}}
            ws.send_json({"type": "pong", "payload": {}})

    await session.stop()


@pytest.mark.asyncio
async def test_heartbeat_pong_is_processed_while_brain_turn_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow brain turn must not block inbound pong handling."""
    monkeypatch.setattr(ws_app_module, "HEARTBEAT_INTERVAL_S", 0.01)
    monkeypatch.setattr(ws_app_module, "HEARTBEAT_TIMEOUT_S", 0.2)
    session = _build_session(
        client_factory_override=lambda options: _DelayedSDKClient(
            options,
            action_runner=lambda _spec: None,
        )
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
            seen_ping = False
            seen_sdk = False
            for _ in range(20):
                envelope = ws.receive_json()
                if envelope["type"] == "ping":
                    seen_ping = True
                    ws.send_json({"type": "pong", "payload": {}})
                    continue
                if envelope["type"] == "sdk_message":
                    seen_sdk = True
                    assert envelope["payload"]["content"][0]["text"] == "slow hi"
                    break

            assert seen_ping
            assert seen_sdk

    await session.stop()


@pytest.mark.asyncio
async def test_run_ws_app_waits_for_startup_and_serves_until_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_ws_app exposes the documented uvicorn entrypoint."""
    created: dict[str, Any] = {}

    class FakeConfig:
        def __init__(self, app: Any, *, host: str, port: int, lifespan: str) -> None:
            self.app = app
            self.host = host
            self.port = port
            self.lifespan = lifespan
            created["config"] = self

    class FakeServer:
        def __init__(self, config: FakeConfig) -> None:
            self.config = config
            self.started = False
            self.should_exit = False
            created["server"] = self

        async def serve(self) -> None:
            self.started = True
            while not self.should_exit:
                await asyncio.sleep(0.01)

    async def stop_after_start() -> None:
        while "server" not in created or not created["server"].started:
            await asyncio.sleep(0.01)
        created["server"].should_exit = True

    monkeypatch.setattr(ws_app_module.uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(ws_app_module.uvicorn, "Server", FakeServer)
    app = build_ws_app(_build_session())
    stopper = asyncio.create_task(stop_after_start())

    await run_ws_app(app, host="127.0.0.1", port=8787, startup_timeout=1.0)
    await stopper

    config = created["config"]
    assert config.app is app
    assert config.host == "127.0.0.1"
    assert config.port == 8787
    assert config.lifespan == "on"


@pytest.mark.asyncio
async def test_legacy_protocol_emits_pipeline_error_and_closes() -> None:
    """Legacy inbound types yield pipeline_error and close the socket."""
    session = _build_session()
    await session.start()
    app = build_ws_app(session)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/agent") as ws:
            ws.send_json({"type": "front_" + "hint_chunk", "payload": {"text": "x"}})
            envelope = ws.receive_json()
            assert envelope["type"] == "pipeline_error"
            assert envelope["payload"]["reason"] == "legacy_protocol_rejected"
            with pytest.raises(Exception):
                ws.receive_json()

    await session.stop()
