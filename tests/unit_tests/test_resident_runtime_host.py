"""Tests for the resident runtime host in ReachyMiniApp."""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from reachy_mini import ReachyMiniApp
from reachy_mini.runtime.scheduler import FrontOutputPacket


class FakeRuntime:
    """Small resident-runtime stub for the app host."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.queues: set[asyncio.Queue[FrontOutputPacket]] = set()

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

    async def handle_user_text(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        user_text: str,
        surface_state_handler=None,
    ) -> None:
        _ = session_id
        _ = user_id
        _ = user_text

        if surface_state_handler is not None:
            await surface_state_handler({"thread_id": thread_id, "phase": "replying"})

        packets = [
            FrontOutputPacket(
                type="front_hint_done",
                thread_id=thread_id,
                turn_id="turn-1",
                text="hint",
            ),
            FrontOutputPacket(
                type="front_final_done",
                thread_id=thread_id,
                turn_id="turn-1",
                text="final reply",
            ),
        ]
        for queue in list(self.queues):
            for packet in packets:
                queue.put_nowait(packet)

    async def wait_for_thread_idle(self, thread_id: str, timeout: float = 600.0) -> None:
        _ = thread_id
        _ = timeout

    def get_thread_surface_state(self, thread_id: str) -> dict[str, str]:
        return {"thread_id": thread_id, "phase": "idle"}


class ConcurrentFakeRuntime(FakeRuntime):
    """Runtime stub that proves websocket dispatch does not block on one turn."""

    def __init__(self) -> None:
        super().__init__()
        self.first_started = threading.Event()
        self.second_started = threading.Event()
        self.allow_first_finish = threading.Event()

    async def handle_user_text(
        self,
        *,
        thread_id: str,
        session_id: str,
        user_id: str,
        user_text: str,
        surface_state_handler=None,
    ) -> None:
        _ = session_id
        _ = user_id

        if surface_state_handler is not None:
            await surface_state_handler({"thread_id": thread_id, "phase": "replying"})

        if user_text == "first":
            self.first_started.set()
            for queue in list(self.queues):
                queue.put_nowait(
                    FrontOutputPacket(
                        type="front_hint_done",
                        thread_id=thread_id,
                        turn_id="turn-first",
                        text="hint:first",
                    )
                )
            await asyncio.to_thread(self.allow_first_finish.wait, 1.0)
            for queue in list(self.queues):
                queue.put_nowait(
                    FrontOutputPacket(
                        type="front_final_done",
                        thread_id=thread_id,
                        turn_id="turn-first",
                        text="final:first",
                    )
                )
            return

        if user_text == "second":
            self.second_started.set()
            for queue in list(self.queues):
                queue.put_nowait(
                    FrontOutputPacket(
                        type="front_hint_done",
                        thread_id=thread_id,
                        turn_id="turn-second",
                        text="hint:second",
                    )
                )
                queue.put_nowait(
                    FrontOutputPacket(
                        type="front_final_done",
                        thread_id=thread_id,
                        turn_id="turn-second",
                        text="final:second",
                    )
                )
            return

        await super().handle_user_text(
            thread_id=thread_id,
            session_id=session_id,
            user_id=user_id,
            user_text=user_text,
            surface_state_handler=surface_state_handler,
        )


class RuntimeHostedApp(ReachyMiniApp):
    """Small ReachyMiniApp subclass that hosts a resident runtime."""

    custom_app_url: str | None = "http://0.0.0.0:8042"

    def __init__(self, profile_root: Path, runtime: FakeRuntime) -> None:
        self.profile_root_relative_path = str(profile_root)
        self._test_runtime = runtime
        super().__init__()

    def build_runtime(self, profile_root: Path) -> FakeRuntime:
        assert profile_root == self.profile_root
        return self._test_runtime


def test_reachy_mini_app_streams_resident_runtime_over_websocket(tmp_path: Path) -> None:
    """ReachyMiniApp should stream resident runtime events over /ws/agent."""

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

                events = [websocket.receive_json() for _ in range(3)]
                event_types = [event["type"] for event in events]

                assert "surface_state" in event_types
                assert "front_hint_done" in event_types
                assert "front_final_done" in event_types

                hint_event = next(
                    event for event in events if event["type"] == "front_hint_done"
                )
                final_event = next(
                    event for event in events if event["type"] == "front_final_done"
                )

                assert hint_event["thread_id"] == "app:test"
                assert hint_event["text"] == "hint"
                assert final_event["thread_id"] == "app:test"
                assert final_event["text"] == "final reply"
                assert runtime.started
    finally:
        stop_event.set()
        worker.join(timeout=5.0)

    assert runtime.stopped


def test_reachy_mini_app_websocket_dispatches_new_turns_while_previous_kernel_work_runs(
    tmp_path: Path,
) -> None:
    """Websocket dispatch should not block later user turns on earlier kernel work."""

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
                status_event = websocket.receive_json()
                assert status_event["type"] == "runtime_status"
                assert status_event["ready"] is True

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

                received_texts: list[str] = []
                for _ in range(6):
                    event = websocket.receive_json()
                    if event["type"] in {"front_hint_done", "front_final_done"}:
                        received_texts.append(str(event["text"]))
                    if "final:first" in received_texts and "final:second" in received_texts:
                        break

                assert "hint:first" in received_texts
                assert "final:first" in received_texts
                assert "hint:second" in received_texts
                assert "final:second" in received_texts
    finally:
        stop_event.set()
        worker.join(timeout=5.0)
