"""Tests for the host-only app web launcher."""

import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from reachy_mini.runtime.project import create_app_project, inspect_app_project
from reachy_mini.runtime.web import build_web_host, resolve_web_binding


def test_resolve_web_binding_maps_wildcard_bind_to_local_browser_url(
    tmp_path: Path,
) -> None:
    """Wildcard bind hosts should still produce a local browser URL."""
    app_root = create_app_project(tmp_path / "demo_web", "demo_web")
    app_project = inspect_app_project(app_root)

    binding = resolve_web_binding(app_project)

    assert binding.host == "0.0.0.0"
    assert binding.port == 8042
    assert binding.browser_url == "http://127.0.0.1:8042/"


def test_host_only_web_launcher_streams_generated_app(tmp_path: Path) -> None:
    """The host-only launcher should serve the generated page and /ws/agent."""
    app_root = create_app_project(tmp_path / "demo_web", "demo_web")
    app_project = inspect_app_project(app_root)
    binding = resolve_web_binding(app_project)
    app = build_web_host(app_project, bind_url=binding.bind_url)

    stop_event = threading.Event()
    worker = threading.Thread(
        target=app.run,
        args=(SimpleNamespace(), stop_event),
        daemon=True,
    )
    worker.start()

    try:
        assert app.wait_until_runtime_ready(timeout=3.0)
        assert app.settings_app is not None

        with TestClient(app.settings_app) as client:
            response = client.get("/")
            assert response.status_code == 200
            assert "Reachy Mini" in response.text

            with client.websocket_connect("/ws/agent") as websocket:
                websocket.send_json(
                    {
                        "type": "browser_input",
                        "ts_ms": 1,
                        "payload": {
                            "kind": "text",
                            "session_id": "app:test",
                            "payload": {"text": "帮我看看日志", "turn_id": "T1"},
                        },
                    }
                )

                seen_brain = False
                seen_action = False
                final_text = ""
                for _ in range(40):
                    envelope = websocket.receive_json()
                    if envelope["type"] == "brain_reply":
                        seen_brain = True
                        final_text = str(envelope["payload"]["reply_text"])
                    if envelope["type"] == "action_result":
                        seen_action = True
                    if seen_brain and seen_action:
                        break

                assert seen_brain
                assert seen_action
                assert final_text == "我听到了：帮我看看日志"
    finally:
        stop_event.set()
        worker.join(timeout=5.0)
