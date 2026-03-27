"""Tests for the thin embodiment coordination layer."""

import asyncio

import numpy as np

from reachy_mini.runtime.embodiment import EmbodimentCoordinator
from reachy_mini.runtime.speech_driver import SpeechDriver
from reachy_mini.runtime.surface_driver import SurfaceDriver


class FakeHeadWobbler:
    """Collect speech-driver calls without spinning audio threads."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.fed: list[str] = []
        self.reset_calls = 0

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def feed(self, delta_b64: str) -> None:
        self.fed.append(delta_b64)

    def reset(self) -> None:
        self.reset_calls += 1


class FakeMovementManager:
    """Collect surface-driver side effects without a real robot."""

    def __init__(self) -> None:
        self.listening_calls: list[bool] = []
        self.activity_marks = 0
        self.queued_moves: list[object] = []
        self.moving_durations: list[float] = []
        self.clear_count = 0

    def set_listening(self, listening: bool) -> None:
        self.listening_calls.append(bool(listening))

    def mark_activity(self) -> None:
        self.activity_marks += 1

    def queue_move(self, move: object) -> None:
        self.queued_moves.append(move)

    def set_moving_state(self, duration: float) -> None:
        self.moving_durations.append(float(duration))

    def clear_move_queue(self) -> None:
        self.clear_count += 1


class FakeReachyMini:
    """Small robot double for coordinator action tests."""

    def __init__(self) -> None:
        self.current_head_pose = np.eye(4, dtype=np.float64)
        self.current_head_joints = [0.12, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.current_antennas = [0.25, -0.25]

    def get_current_head_pose(self) -> np.ndarray:
        return self.current_head_pose.copy()

    def get_current_joint_positions(self) -> tuple[list[float], list[float]]:
        return list(self.current_head_joints), list(self.current_antennas)


class FakeCameraWorker:
    """Collect coordinator head-tracking toggles."""

    def __init__(self) -> None:
        self.enabled_states: list[bool] = []

    def set_head_tracking_enabled(self, enabled: bool) -> None:
        self.enabled_states.append(bool(enabled))


class FakeRecordedMoves:
    """Small recorded-moves stub for coordinator action tests."""

    def __init__(self, names: list[str]) -> None:
        self._names = list(names)

    def get(self, move_name: str) -> str:
        return f"move:{move_name}"


def test_speech_driver_wraps_head_wobbler_feed_and_reset() -> None:
    """SpeechDriver should formalize the existing head-wobbler helper interface."""
    head_wobbler = FakeHeadWobbler()
    driver = SpeechDriver(head_wobbler=head_wobbler)

    assert driver.start() is True
    assert head_wobbler.started is True
    assert driver.feed_audio_delta("demo-audio") is True
    assert head_wobbler.fed == ["demo-audio"]
    assert driver.speech_active is True

    assert driver.reset_speech_motion() is True
    assert head_wobbler.reset_calls == 1
    assert driver.speech_active is False
    assert driver.stop() is True
    assert head_wobbler.stopped is True


def test_speech_driver_resets_stale_replying_motion() -> None:
    """Replying speech motion should auto-clear once audio has gone stale."""

    fake_time = {"value": 10.0}
    head_wobbler = FakeHeadWobbler()
    driver = SpeechDriver(
        head_wobbler=head_wobbler,
        speech_idle_timeout_s=0.3,
        now_fn=lambda: fake_time["value"],
    )

    assert driver.feed_audio_delta("demo-audio") is True
    assert driver.speech_active is True
    assert driver.current_phase == "replying"

    fake_time["value"] = 10.2
    assert driver.apply_phase("replying") is False
    assert driver.speech_active is True
    assert head_wobbler.reset_calls == 0

    fake_time["value"] = 10.31
    assert driver.apply_phase("replying") is True
    assert driver.speech_active is False
    assert head_wobbler.reset_calls == 1


def test_embodiment_coordinator_resets_speech_when_leaving_replying() -> None:
    """Surface lifecycle should clear residual speech motion once replying ends."""
    movement_manager = FakeMovementManager()
    surface_driver = SurfaceDriver(movement_manager=movement_manager)
    speech_driver = SpeechDriver(head_wobbler=FakeHeadWobbler())
    coordinator = EmbodimentCoordinator(
        surface_driver=surface_driver,
        speech_driver=speech_driver,
    )

    assert coordinator.feed_audio_delta("demo-audio") is True
    assert speech_driver.speech_active is True

    assert coordinator.apply_surface_state({"thread_id": "app:test", "phase": "replying"}) == "replying"
    assert speech_driver.speech_active is True

    assert coordinator.apply_surface_state({"thread_id": "app:test", "phase": "settling"}) == "settling"
    assert speech_driver.speech_active is False
    assert speech_driver.head_wobbler.reset_calls == 1


def test_embodiment_coordinator_exposes_current_surface_state_and_settling_hold() -> None:
    """Coordinator should surface the aggregate embodied state, not only the phase string."""
    fake_time = {"value": 10.0}
    coordinator = EmbodimentCoordinator(
        surface_driver=SurfaceDriver(now_fn=lambda: fake_time["value"]),
        now_fn=lambda: fake_time["value"],
    )

    assert (
        coordinator.apply_surface_state(
            {
                "thread_id": "app:test",
                "phase": "replying",
                "source_signal": "kernel_output_ready",
            }
        )
        == "replying"
    )
    assert coordinator.current_surface_state["source_signal"] == "kernel_output_ready"

    assert (
        coordinator.apply_surface_state(
            {
                "thread_id": "app:test",
                "phase": "settling",
                "recommended_hold_ms": 900,
            }
        )
        == "settling"
    )
    assert coordinator.apply_surface_state({"thread_id": "app:test", "phase": "idle"}) == "settling"
    assert coordinator.current_phase == "settling"
    assert coordinator.current_surface_state["recommended_hold_ms"] == 900

    fake_time["value"] = 10.95

    assert coordinator.current_phase == "idle"
    assert coordinator.current_surface_state == {"phase": "idle"}


def test_embodiment_coordinator_handles_explicit_motion_actions() -> None:
    """Coordinator should own explicit expressive body actions as Stage 4 grows."""

    async def _exercise() -> None:
        movement_manager = FakeMovementManager()
        camera_worker = FakeCameraWorker()
        coordinator = EmbodimentCoordinator(
            reachy_mini=FakeReachyMini(),
            movement_manager=movement_manager,
            camera_worker=camera_worker,
            motion_duration_s=1.25,
        )

        assert coordinator.set_head_tracking(True) == "Head tracking started"
        assert (
            coordinator.dance("simple_nod", 2, FakeRecordedMoves(["simple_nod"]))
            == "Playing dance simple_nod x2"
        )
        assert (
            coordinator.play_emotion("happy", FakeRecordedMoves(["happy"]))
            == "Playing emotion happy (preempted dance)"
        )
        assert await coordinator.move_head("left") == "Moved head left (preempted emotion)"
        assert coordinator.set_head_tracking(True) == "Head tracking deferred until motion settles"
        assert coordinator.clear_motion_queue(label="dance") == "Stopped dance and cleared queue"

        assert len(movement_manager.queued_moves) == 4
        assert movement_manager.queued_moves[0].__class__.__name__ == "DanceQueueMove"
        assert movement_manager.queued_moves[1].__class__.__name__ == "DanceQueueMove"
        assert movement_manager.queued_moves[2].__class__.__name__ == "EmotionQueueMove"
        assert movement_manager.queued_moves[3].__class__.__name__ == "GotoQueueMove"
        assert movement_manager.moving_durations == [1.25]
        assert camera_worker.enabled_states == [True, False, True]
        assert movement_manager.clear_count == 3

    asyncio.run(_exercise())


def test_embodiment_coordinator_preempts_dance_with_move_head() -> None:
    """A direct head move should interrupt a lower-priority dance immediately."""

    async def _exercise() -> None:
        movement_manager = FakeMovementManager()
        coordinator = EmbodimentCoordinator(
            reachy_mini=FakeReachyMini(),
            movement_manager=movement_manager,
            motion_duration_s=1.25,
            now_fn=lambda: 20.0,
        )

        assert (
            coordinator.dance("simple_nod", 1, FakeRecordedMoves(["simple_nod"]))
            == "Playing dance simple_nod x1"
        )
        assert await coordinator.move_head("right") == "Moved head right (preempted dance)"
        assert movement_manager.clear_count == 1
        assert [move.__class__.__name__ for move in movement_manager.queued_moves] == [
            "DanceQueueMove",
            "GotoQueueMove",
        ]

    asyncio.run(_exercise())


def test_embodiment_coordinator_defers_lower_priority_motion_during_move_head() -> None:
    """Lower-priority expressive moves should wait while a stronger motion is active."""

    async def _exercise() -> None:
        movement_manager = FakeMovementManager()
        coordinator = EmbodimentCoordinator(
            reachy_mini=FakeReachyMini(),
            movement_manager=movement_manager,
            motion_duration_s=1.25,
            now_fn=lambda: 30.0,
        )

        assert await coordinator.move_head("left") == "Moved head left"
        assert (
            coordinator.dance("simple_nod", 1, FakeRecordedMoves(["simple_nod"]))
            == "Deferred dance simple_nod x1 while move_head is active"
        )
        assert movement_manager.clear_count == 0
        assert [move.__class__.__name__ for move in movement_manager.queued_moves] == [
            "GotoQueueMove"
        ]

    asyncio.run(_exercise())


def test_embodiment_coordinator_suspends_and_resumes_head_tracking_around_motion() -> None:
    """Explicit motion should suspend tracking, then restore it once the motion window ends."""
    fake_time = {"value": 10.0}
    speech_driver = SpeechDriver(head_wobbler=FakeHeadWobbler())
    coordinator = EmbodimentCoordinator(
        reachy_mini=FakeReachyMini(),
        movement_manager=FakeMovementManager(),
        camera_worker=FakeCameraWorker(),
        speech_driver=speech_driver,
        motion_duration_s=1.25,
        now_fn=lambda: fake_time["value"],
    )

    assert coordinator.set_head_tracking(True) == "Head tracking started"
    assert coordinator.camera_worker.enabled_states == [True]

    assert coordinator.feed_audio_delta("demo-audio") is True
    assert speech_driver.speech_active is True

    asyncio.run(coordinator.move_head("left"))
    assert coordinator.camera_worker.enabled_states == [True, False]
    assert speech_driver.speech_active is False

    fake_time["value"] = 10.5
    assert (
        coordinator.set_head_tracking(True)
        == "Head tracking deferred until motion settles"
    )
    assert coordinator.camera_worker.enabled_states == [True, False]

    fake_time["value"] = 11.6
    assert coordinator.current_phase == "idle"
    assert coordinator.current_surface_state == {"phase": "idle"}
    assert coordinator.camera_worker.enabled_states == [True, False, True]
