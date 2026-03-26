"""Tests for Reachy-specific runtime tools."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np

from reachy_mini.runtime.tools import (
    CameraTool,
    DanceTool,
    DoNothingTool,
    HeadTrackingTool,
    MoveHeadTool,
    PlayEmotionTool,
    ReachyToolContext,
    StopDanceTool,
    StopEmotionTool,
    build_system_tools,
)


class FakeReachyMini:
    """Small robot test double used by the Reachy runtime tools."""

    def __init__(self) -> None:
        self.goto_calls: list[dict[str, Any]] = []
        self.set_calls: list[dict[str, Any]] = []
        self.current_head_pose = np.eye(4, dtype=np.float64)
        self.current_head_joints = [0.12, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.current_antennas = [0.25, -0.25]

    def goto_target(
        self,
        *,
        head: Any = None,
        antennas: Any = None,
        duration: float = 0.5,
        method: Any = None,
        body_yaw: float | None = 0.0,
    ) -> None:
        self.goto_calls.append(
            {
                "head": head,
                "antennas": antennas,
                "duration": duration,
                "method": method,
                "body_yaw": body_yaw,
            }
        )

    def set_target(
        self,
        *,
        head: Any = None,
        antennas: Any = None,
        body_yaw: float | None = None,
    ) -> None:
        self.set_calls.append(
            {"head": head, "antennas": antennas, "body_yaw": body_yaw}
        )

    def get_current_head_pose(self) -> np.ndarray:
        return self.current_head_pose.copy()

    def get_current_joint_positions(self) -> tuple[list[float], list[float]]:
        return list(self.current_head_joints), list(self.current_antennas)


class FakeMovementManager:
    """Collect queue operations without starting a real control loop."""

    def __init__(self) -> None:
        self.queued_moves: list[Any] = []
        self.moving_durations: list[float] = []
        self.clear_count = 0

    def queue_move(self, move: Any) -> None:
        self.queued_moves.append(move)

    def set_moving_state(self, duration: float) -> None:
        self.moving_durations.append(float(duration))

    def clear_move_queue(self) -> None:
        self.clear_count += 1


class FakeCameraWorker:
    """Small camera-worker test double."""

    def __init__(self) -> None:
        self.enabled_states: list[bool] = []
        self.latest_frame: Any | None = None

    def set_head_tracking_enabled(self, enabled: bool) -> None:
        self.enabled_states.append(bool(enabled))

    def get_latest_frame(self) -> Any | None:
        return self.latest_frame


class FakeVisionProcessor:
    """Small vision-processor test double."""

    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[tuple[Any, str]] = []

    def process_image(self, frame: Any, question: str) -> str:
        self.calls.append((frame, question))
        return self.result


class FakeRecordedMoves:
    """Small recorded-moves stub."""

    def __init__(self, names: list[str]) -> None:
        self._names = list(names)

    def list_moves(self) -> list[str]:
        return list(self._names)

    def get(self, move_name: str) -> str:
        return f"move:{move_name}"


def test_build_system_tools_includes_reachy_runtime_tools(tmp_path: Path) -> None:
    """The shared system tool set should include the migrated Reachy tools."""
    tools = build_system_tools(tmp_path)
    names = {tool.name for tool in tools if hasattr(tool, "name")}

    assert "move_head" in names
    assert "do_nothing" in names
    assert "head_tracking" in names
    assert "camera" in names
    assert "play_emotion" in names
    assert "dance" in names
    assert "stop_emotion" in names
    assert "stop_dance" in names


def test_move_head_tool_queues_goto_move() -> None:
    """move_head should queue a goto move through the movement manager."""

    async def _exercise() -> None:
        robot = FakeReachyMini()
        movement_manager = FakeMovementManager()
        tool = MoveHeadTool(
            context=ReachyToolContext(
                reachy_mini=robot,
                motion_duration_s=1.25,
                movement_manager=movement_manager,
            )
        )

        result = await tool.execute(direction="left")

        assert result == "Moved head left"
        assert len(movement_manager.queued_moves) == 1
        queued_move = movement_manager.queued_moves[0]
        assert queued_move.__class__.__name__ == "GotoQueueMove"
        assert queued_move.duration == 1.25
        assert queued_move.start_body_yaw == 0.12
        assert queued_move.start_antennas == (0.25, -0.25)
        assert movement_manager.moving_durations == [1.25]

    asyncio.run(_exercise())


def test_head_tracking_tool_toggles_camera_worker() -> None:
    """head_tracking should toggle the configured camera worker."""

    async def _exercise() -> None:
        camera_worker = FakeCameraWorker()
        tool = HeadTrackingTool(context=ReachyToolContext(camera_worker=camera_worker))

        start_result = await tool.execute(start=True)
        stop_result = await tool.execute(start=False)

        assert start_result == "Head tracking started"
        assert stop_result == "Head tracking stopped"
        assert camera_worker.enabled_states == [True, False]

    asyncio.run(_exercise())


def test_do_nothing_tool_returns_explicit_status() -> None:
    """do_nothing should return a human-readable no-op status."""

    async def _exercise() -> None:
        result = await DoNothingTool().execute(reason="waiting calmly")
        assert result == "Staying still: waiting calmly"

    asyncio.run(_exercise())


def test_camera_tool_uses_local_vision_processor_when_available() -> None:
    """camera should use the configured vision processor before JPEG fallback."""

    async def _exercise() -> None:
        camera_worker = FakeCameraWorker()
        camera_worker.latest_frame = [[1, 2], [3, 4]]
        vision_processor = FakeVisionProcessor("A red cup on a table.")
        tool = CameraTool(
            context=ReachyToolContext(
                camera_worker=camera_worker,
                vision_processor=vision_processor,
            )
        )

        result = await tool.execute(question="What do you see?")

        assert result == {"image_description": "A red cup on a table."}
        assert vision_processor.calls == [([[1, 2], [3, 4]], "What do you see?")]

    asyncio.run(_exercise())


def test_camera_tool_returns_base64_image_when_no_vision_processor() -> None:
    """camera should JPEG-encode the latest frame when only the camera worker is available."""

    async def _exercise() -> None:
        camera_worker = FakeCameraWorker()
        camera_worker.latest_frame = "frame"
        tool = CameraTool(context=ReachyToolContext(camera_worker=camera_worker))

        with patch(
            "cv2.imencode",
            return_value=(True, np.frombuffer(b"jpeg-bytes", dtype=np.uint8)),
        ):
            result = await tool.execute(question="What color is this?")

        assert result == {"b64_im": "anBlZy1ieXRlcw=="}

    asyncio.run(_exercise())


def test_play_emotion_tool_queues_emotion_move() -> None:
    """play_emotion should enqueue an EmotionQueueMove through the movement manager."""

    async def _exercise() -> None:
        movement_manager = FakeMovementManager()
        context = ReachyToolContext(
            reachy_mini=FakeReachyMini(),
            movement_manager=movement_manager,
        )
        tool = PlayEmotionTool(context=context)

        with patch(
            "reachy_mini.runtime.tools.reachy_tools._load_recorded_moves",
            return_value=FakeRecordedMoves(["happy", "curious"]),
        ):
            result = await tool.execute(emotion="happy")

        assert result == "Playing emotion happy"
        assert len(movement_manager.queued_moves) == 1
        queued_move = movement_manager.queued_moves[0]
        assert queued_move.__class__.__name__ == "EmotionQueueMove"
        assert queued_move.emotion_name == "happy"

    asyncio.run(_exercise())


def test_dance_tool_and_stop_dance_tool_use_movement_manager() -> None:
    """dance should queue dance moves and stop_dance should clear the queue."""

    async def _exercise() -> None:
        movement_manager = FakeMovementManager()
        context = ReachyToolContext(
            reachy_mini=FakeReachyMini(),
            movement_manager=movement_manager,
        )
        dance_tool = DanceTool(context=context)
        stop_tool = StopDanceTool(context=context)

        with patch(
            "reachy_mini.runtime.tools.reachy_tools._load_recorded_moves",
            return_value=FakeRecordedMoves(["simple_nod", "head_tilt_roll"]),
        ):
            result = await dance_tool.execute(move="simple_nod", repeat=2)
            stop_result = await stop_tool.execute()

        assert result == "Playing dance simple_nod x2"
        assert len(movement_manager.queued_moves) == 2
        assert all(
            queued_move.__class__.__name__ == "DanceQueueMove"
            for queued_move in movement_manager.queued_moves
        )
        assert all(
            queued_move.move_name == "simple_nod"
            for queued_move in movement_manager.queued_moves
        )
        assert stop_result == "Stopped dance and cleared queue"
        assert movement_manager.clear_count == 1

    asyncio.run(_exercise())


def test_stop_emotion_tool_clears_queue() -> None:
    """stop_emotion should clear the queued movement state."""

    async def _exercise() -> None:
        movement_manager = FakeMovementManager()
        context = ReachyToolContext(
            reachy_mini=FakeReachyMini(),
            movement_manager=movement_manager,
        )
        result = await StopEmotionTool(context=context).execute()
        assert result == "Stopped emotion and cleared queue"
        assert movement_manager.clear_count == 1

    asyncio.run(_exercise())
