"""Tests for the resident runtime host in ReachyMiniApp."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from reachy_mini import ReachyMiniApp
from reachy_mini.apps.app import ChatRequest
from reachy_mini.runtime.scheduler import FrontOutputPacket


class FakeRuntime:
    """Small resident-runtime stub for the app host."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.queues: set[asyncio.Queue[FrontOutputPacket]] = set()
        self.speech_events: list[dict[str, str]] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def subscribe_front_outputs(self) -> asyncio.Queue[FrontOutputPacket]:
        queue: asyncio.Queue[FrontOutputPacket] = asyncio.Queue()
        self.queues.add(queue)
        return queue

    def unsubscribe_front_outputs(self, queue: asyncio.Queue[FrontOutputPacket]) -> None:
        self.queues.discard(queue)

    async def handle_user_turn(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        user_text: str,
        surface_state_handler=None,
        final_reply_handler=None,
    ) -> str:
        _ = session_id, user_id, user_text
        if surface_state_handler is not None:
            await surface_state_handler({"thread_id": thread_id, "phase": "replying"})
        if final_reply_handler is not None:
            await final_reply_handler(
                {
                    "thread_id": thread_id,
                    "turn_id": "turn-1",
                    "text": "final reply",
                }
            )

        packets = [
            FrontOutputPacket(
                type="thinking",
                thread_id=thread_id,
                turn_id="turn-1",
                text="正在思考",
                payload={"phase": "start", "source": "status"},
            ),
            FrontOutputPacket(
                type="tool_progress",
                thread_id=thread_id,
                turn_id="turn-1",
                text="camera ready",
                payload={"tool_name": "camera", "tool_use_id": "tool-1"},
            ),
            FrontOutputPacket(
                type="text_delta",
                thread_id=thread_id,
                turn_id="turn-1",
                text="final reply",
            ),
            FrontOutputPacket(
                type="turn_done",
                thread_id=thread_id,
                turn_id="turn-1",
                text="final reply",
            ),
        ]
        for queue in list(self.queues):
            for packet in packets:
                queue.put_nowait(packet)
        return "turn-1"

    async def wait_for_thread_idle(self, thread_id: str, timeout: float = 600.0) -> None:
        _ = thread_id, timeout

    async def handle_user_speech_started(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        user_text: str = "",
        surface_state_handler=None,
    ) -> None:
        _ = session_id, user_id
        self.speech_events.append(
            {
                "type": "user_speech_started",
                "thread_id": thread_id,
                "text": user_text,
            }
        )
        if surface_state_handler is not None:
            await surface_state_handler({"thread_id": thread_id, "phase": "listening"})

    async def handle_user_speech_stopped(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        user_text: str = "",
        surface_state_handler=None,
    ) -> None:
        _ = session_id, user_id
        self.speech_events.append(
            {
                "type": "user_speech_stopped",
                "thread_id": thread_id,
                "text": user_text,
            }
        )
        if surface_state_handler is not None:
            await surface_state_handler(
                {"thread_id": thread_id, "phase": "listening_wait"}
            )

    def get_thread_surface_state(self, thread_id: str) -> dict[str, str]:
        return {"thread_id": thread_id, "phase": "idle"}


class ConcurrentFakeRuntime(FakeRuntime):
    """Runtime stub that proves websocket dispatch does not block on one turn."""

    def __init__(self) -> None:
        super().__init__()
        self.first_started = threading.Event()
        self.second_started = threading.Event()
        self.allow_first_finish = threading.Event()

    async def handle_user_turn(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        user_text: str,
        surface_state_handler=None,
        final_reply_handler=None,
    ) -> str:
        _ = session_id, user_id, final_reply_handler
        if surface_state_handler is not None:
            await surface_state_handler({"thread_id": thread_id, "phase": "replying"})

        if user_text == "first":
            self.first_started.set()
            for queue in list(self.queues):
                queue.put_nowait(
                    FrontOutputPacket(
                        type="thinking",
                        thread_id=thread_id,
                        turn_id="turn-first",
                        text="thinking:first",
                    )
                )
            await asyncio.to_thread(self.allow_first_finish.wait, 1.0)
            for queue in list(self.queues):
                queue.put_nowait(
                    FrontOutputPacket(
                        type="turn_done",
                        thread_id=thread_id,
                        turn_id="turn-first",
                        text="final:first",
                    )
                )
            return "turn-first"

        if user_text == "second":
            self.second_started.set()
            for queue in list(self.queues):
                queue.put_nowait(
                    FrontOutputPacket(
                        type="turn_done",
                        thread_id=thread_id,
                        turn_id="turn-second",
                        text="final:second",
                    )
                )
            return "turn-second"

        return await super().handle_user_turn(
            thread_id=thread_id,
            session_id=session_id,
            user_id=user_id,
            user_text=user_text,
            surface_state_handler=surface_state_handler,
            final_reply_handler=final_reply_handler,
        )


class FakeSurfaceDriver:
    """Collect runtime surface states forwarded by the app host."""

    def __init__(self) -> None:
        self.states: list[dict[str, str]] = []

    def apply_state(self, state: dict[str, str]) -> None:
        self.states.append(dict(state))


class RuntimeHostedApp(ReachyMiniApp):
    """Tiny ReachyMiniApp that injects a fake resident runtime."""

    custom_app_url = "http://127.0.0.1:8042"

    def __init__(self, profile_root: Path, runtime: FakeRuntime) -> None:
        self.profile_root_relative_path = None
        self._profile_root = profile_root
        self._runtime = runtime
        super().__init__()

    def resolve_profile_root(self) -> Path | None:
        return self._profile_root

    def _get_instance_path(self) -> Path:
        return self._profile_root / "demo.py"

    def build_runtime(self, profile_root: Path):
        _ = profile_root
        return self._runtime


def test_reachy_mini_app_streams_single_brain_events_over_websocket(
    tmp_path: Path,
) -> None:
    """ReachyMiniApp should stream single-brain runtime events over /ws/agent."""

    runtime = FakeRuntime()
    app = RuntimeHostedApp(tmp_path / "profiles", runtime)
    stop_event = threading.Event()
    worker = threading.Thread(
        target=app.run,
        args=(SimpleNamespace(), stop_event),
        daemon=True,
    )
    worker.start()

    try:
        assert app.wait_until_runtime_ready(timeout=2.0)
        assert app.settings_app is not None
        surface_driver = FakeSurfaceDriver()
        app.runtime_tool_context = SimpleNamespace(surface_driver=surface_driver)

        with TestClient(app.settings_app) as client:
            with client.websocket_connect("/ws/agent") as websocket:
                status_event = websocket.receive_json()
                assert status_event["type"] == "runtime_status"
                assert status_event["ready"] is True

                websocket.send_json(
                    {
                        "type": "user_text",
                        "thread_id": "app:test",
                        "text": "帮我看看现在是不是已经接入流程了",
                    }
                )

                events = [websocket.receive_json() for _ in range(6)]
                event_types = [event["type"] for event in events]

                assert "surface_state" in event_types
                assert "thinking" in event_types
                assert "tool_progress" in event_types
                assert "turn_done" in event_types

                done_event = next(event for event in events if event["type"] == "turn_done")
                assert done_event["text"] == "final reply"
                assert surface_driver.states[-1]["phase"] == "replying"
    finally:
        stop_event.set()
        worker.join(timeout=5.0)


def test_reachy_mini_app_chat_returns_single_brain_response(tmp_path: Path) -> None:
    """The blocking chat helper should return the new single-brain response shape."""

    runtime = FakeRuntime()
    app = RuntimeHostedApp(tmp_path / "profiles", runtime)
    stop_event = threading.Event()
    worker = threading.Thread(
        target=app.run,
        args=(SimpleNamespace(), stop_event),
        daemon=True,
    )
    worker.start()

    try:
        assert app.wait_until_runtime_ready(timeout=2.0)
        response = app.chat(
            ChatRequest(
                message="你好",
                thread_id="app:test",
                session_id="sess-1",
                user_id="user-1",
            )
        )

        assert response.turn_id == "turn-1"
        assert response.reply == "final reply"
        assert response.error == ""
        assert response.surface_state is not None
        assert response.surface_state["phase"] == "idle"
        assert response.thinking
        assert response.thinking[0]["text"] == "正在思考"
        assert response.tool_progress
        assert response.tool_progress[0]["tool_name"] == "camera"
    finally:
        stop_event.set()
        worker.join(timeout=5.0)


def test_reachy_mini_app_chat_invokes_runtime_reply_audio_handler(tmp_path: Path) -> None:
    """The app should still route final replies into the reply-audio handler."""

    runtime = FakeRuntime()
    app = RuntimeHostedApp(tmp_path / "profiles", runtime)
    played_payloads: list[dict[str, str]] = []

    async def fake_play_runtime_reply_audio(payload: dict[str, str]) -> bool:
        played_payloads.append(dict(payload))
        return True

    app.play_runtime_reply_audio = fake_play_runtime_reply_audio  # type: ignore[method-assign]

    stop_event = threading.Event()
    worker = threading.Thread(
        target=app.run,
        args=(SimpleNamespace(), stop_event),
        daemon=True,
    )
    worker.start()

    try:
        assert app.wait_until_runtime_ready(timeout=2.0)
        response = app.chat(ChatRequest(message="你好", thread_id="app:test"))
        assert response.reply == "final reply"
        assert played_payloads
        assert played_payloads[0]["turn_id"] == "turn-1"
        assert played_payloads[0]["text"] == "final reply"
    finally:
        stop_event.set()
        worker.join(timeout=5.0)


def test_reachy_mini_app_websocket_accepts_user_speech_lifecycle_events(
    tmp_path: Path,
) -> None:
    """The websocket host should still dispatch browser speech lifecycle events."""

    runtime = FakeRuntime()
    app = RuntimeHostedApp(tmp_path / "profiles", runtime)
    stop_event = threading.Event()
    worker = threading.Thread(
        target=app.run,
        args=(SimpleNamespace(), stop_event),
        daemon=True,
    )
    worker.start()

    try:
        assert app.wait_until_runtime_ready(timeout=2.0)
        assert app.settings_app is not None

        with TestClient(app.settings_app) as client:
            with client.websocket_connect("/ws/agent") as websocket:
                websocket.receive_json()

                websocket.send_json(
                    {
                        "type": "user_speech_started",
                        "thread_id": "app:test",
                        "text": "你好",
                    }
                )
                for _ in range(3):
                    event = websocket.receive_json()
                    if event["type"] == "surface_state" and event["state"]["phase"] == "listening":
                        break
                assert event["type"] == "surface_state"
                assert event["state"]["phase"] == "listening"

                websocket.send_json(
                    {
                        "type": "user_speech_stopped",
                        "thread_id": "app:test",
                        "text": "你好",
                    }
                )
                for _ in range(3):
                    event = websocket.receive_json()
                    if event["type"] == "surface_state" and event["state"]["phase"] == "listening_wait":
                        break
                assert event["type"] == "surface_state"
                assert event["state"]["phase"] == "listening_wait"

        assert runtime.speech_events == [
            {"type": "user_speech_started", "thread_id": "app:test", "text": "你好"},
            {"type": "user_speech_stopped", "thread_id": "app:test", "text": "你好"},
        ]
    finally:
        stop_event.set()
        worker.join(timeout=5.0)


def test_reachy_mini_app_websocket_dispatches_new_turns_while_previous_runtime_work_runs(
    tmp_path: Path,
) -> None:
    """The websocket host should keep accepting turns while one handler is still running."""

    runtime = ConcurrentFakeRuntime()
    app = RuntimeHostedApp(tmp_path / "profiles", runtime)
    stop_event = threading.Event()
    worker = threading.Thread(
        target=app.run,
        args=(SimpleNamespace(), stop_event),
        daemon=True,
    )
    worker.start()

    try:
        assert app.wait_until_runtime_ready(timeout=2.0)
        assert app.settings_app is not None

        with TestClient(app.settings_app) as client:
            with client.websocket_connect("/ws/agent") as websocket:
                websocket.receive_json()

                websocket.send_json(
                    {
                        "type": "user_text",
                        "thread_id": "app:test",
                        "text": "first",
                    }
                )
                assert runtime.first_started.wait(timeout=1.0)

                websocket.send_json(
                    {
                        "type": "user_text",
                        "thread_id": "app:test",
                        "text": "second",
                    }
                )
                assert runtime.second_started.wait(timeout=1.0)
                runtime.allow_first_finish.set()

                done_events: list[dict[str, str]] = []
                for _ in range(8):
                    event = websocket.receive_json()
                    if event["type"] == "turn_done":
                        done_events.append(event)
                    if len(done_events) >= 2:
                        break

        texts = {event["text"] for event in done_events}
        assert "final:first" in texts
        assert "final:second" in texts
    finally:
        stop_event.set()
        worker.join(timeout=5.0)
