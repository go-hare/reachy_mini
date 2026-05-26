"""Integration tests for ReachyMiniApp wired to the v4 RuntimeSession."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from reachy_mini.apps.app import ReachyMiniApp
from reachy_mini.pipeline.session import RuntimeSession


REPO_ROOT = Path(__file__).resolve().parents[3]
SIM_FRONT_APP = REPO_ROOT / "profiles" / "sim_front_app"


_MOCK_OVERRIDES = {
    "model.provider": "mock",
    "model.model": "v4_app_mock",
    "model.base_url": None,
    "model.api_key_ref": "",
    "model.api_key": "",
}


class _HostedSimFrontApp(ReachyMiniApp):
    """Test fixture that points one app at the sim_front_app profile root."""

    custom_app_url = "http://127.0.0.1:8765"
    profile_root_relative_path = str(SIM_FRONT_APP / "profiles")

    def _get_instance_path(self) -> Path:
        return SIM_FRONT_APP / "sim_front_app" / "main.py"

    def build_runtime(self, profile_root: Path) -> RuntimeSession:
        return RuntimeSession.from_profile(
            profile_root,
            overrides=dict(_MOCK_OVERRIDES),
        )


@pytest.fixture()
def hosted_app() -> _HostedSimFrontApp:
    """Start one ReachyMiniApp on a background thread loop."""
    app = _HostedSimFrontApp()
    stop_event = threading.Event()

    def runner() -> None:
        try:
            app.run(SimpleNamespace(), stop_event)
        except BaseException:
            stop_event.set()
            raise

    worker = threading.Thread(target=runner, daemon=True)
    worker.start()
    started = app.wait_until_runtime_ready(timeout=8.0)
    if not started:
        stop_event.set()
        worker.join(timeout=2.0)
        pytest.fail("Resident runtime did not become ready in time.")
    yield app
    stop_event.set()
    worker.join(timeout=5.0)


def test_settings_app_websocket_runs_v4_text_turn(hosted_app: _HostedSimFrontApp) -> None:
    """A browser_input(text) turn produces brain_reply + action_result over /ws/agent."""
    app = hosted_app.settings_app
    assert app is not None
    with TestClient(app) as client:
        with client.websocket_connect("/ws/agent") as ws:
            ws.send_json(
                {
                    "type": "browser_input",
                    "ts_ms": 1,
                    "payload": {
                        "kind": "text",
                        "session_id": "S1",
                        "payload": {"text": "你好", "turn_id": "T1"},
                    },
                }
            )
            seen_brain = False
            seen_action = False
            for _ in range(40):
                envelope = ws.receive_json()
                if envelope["type"] == "brain_reply":
                    seen_brain = True
                if envelope["type"] == "action_result":
                    seen_action = True
                if seen_brain and seen_action:
                    break
            assert seen_brain, "expected a brain_reply frame"
            assert seen_action, "expected an action_result frame"


def test_settings_app_rejects_legacy_protocol(hosted_app: _HostedSimFrontApp) -> None:
    """The hosted app rejects legacy front_* events with pipeline_error."""
    app = hosted_app.settings_app
    assert app is not None
    with TestClient(app) as client:
        with client.websocket_connect("/ws/agent") as ws:
            ws.send_json({"type": "front_hint_chunk", "payload": {"text": "x"}})
            envelope = ws.receive_json()
            assert envelope["type"] == "pipeline_error"
            assert envelope["payload"]["reason"] == "legacy_protocol_rejected"
