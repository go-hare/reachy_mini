"""Reachy-specific runtime tools loaded as part of the system tool set."""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from functools import lru_cache
from inspect import isawaitable
from typing import TYPE_CHECKING, Any

from reachy_mini.runtime.dance_emotion_moves import (
    DanceQueueMove,
    EmotionQueueMove,
    GotoQueueMove,
)
from reachy_mini.runtime.moves import MovementManager
from reachy_mini.runtime.tools.base import Tool
from reachy_mini.utils import create_head_pose

logger = logging.getLogger(__name__)

EMOTIONS_DATASET = "pollen-robotics/reachy-mini-emotions-library"
DANCES_DATASET = "pollen-robotics/reachy-mini-dances-library"

if TYPE_CHECKING:
    from reachy_mini.runtime.embodiment import EmbodimentCoordinator
    from reachy_mini.runtime.speech_driver import SpeechDriver
    from reachy_mini.runtime.surface_driver import SurfaceDriver


@lru_cache(maxsize=4)
def _load_recorded_moves(dataset_name: str) -> Any:
    """Load one recorded-moves dataset lazily."""
    from reachy_mini.motion.recorded_move import RecordedMoves

    return RecordedMoves(dataset_name)


@dataclass(slots=True, frozen=True)
class ReachyToolContext:
    """Runtime dependencies that Reachy-oriented tools may need."""

    reachy_mini: Any | None = None
    camera_worker: Any | None = None
    vision_processor: Any | None = None
    motion_duration_s: float = 1.0
    movement_manager: MovementManager | None = None
    head_wobbler: Any | None = None
    speech_driver: SpeechDriver | None = None
    surface_driver: SurfaceDriver | None = None
    embodiment_coordinator: EmbodimentCoordinator | None = None


class ReachyRuntimeTool(Tool):
    """Small base class for tools that use optional Reachy runtime dependencies."""

    def __init__(self, context: ReachyToolContext | None = None) -> None:
        self.context = context or ReachyToolContext()

    async def _call_coordinator(
        self,
        method_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any | None:
        """Call one coordinator method when the embodiment layer is configured."""

        coordinator = self.context.embodiment_coordinator
        if coordinator is None:
            return None
        method = getattr(coordinator, method_name, None)
        if not callable(method):
            return None
        result = method(*args, **kwargs)
        if isawaitable(result):
            return await result
        return result


class DoNothingTool(ReachyRuntimeTool):
    """Explicitly choose to stay still."""

    @property
    def name(self) -> str:
        return "do_nothing"

    @property
    def description(self) -> str:
        return (
            "Choose to do nothing and stay still. "
            "Use when the best action is to pause instead of moving the robot."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Optional reason for staying still.",
                },
            },
            "required": [],
        }

    async def execute(self, reason: str = "", **kwargs: Any) -> str:
        _ = kwargs
        resolved_reason = str(reason or "").strip() or "no movement requested"
        return f"Staying still: {resolved_reason}"


class HeadTrackingTool(ReachyRuntimeTool):
    """Toggle camera-worker head tracking."""

    @property
    def name(self) -> str:
        return "head_tracking"

    @property
    def description(self) -> str:
        return "Enable or disable head tracking when a camera worker is configured."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "start": {
                    "type": "boolean",
                    "description": "True to enable head tracking, false to disable it.",
                },
            },
            "required": ["start"],
        }

    async def execute(self, start: bool, **kwargs: Any) -> str:
        _ = kwargs
        coordinated = await self._call_coordinator("set_head_tracking", bool(start))
        if coordinated is not None:
            return str(coordinated)

        camera_worker = self.context.camera_worker
        if camera_worker is None or not hasattr(
            camera_worker,
            "set_head_tracking_enabled",
        ):
            return "Error: head_tracking requires a configured camera_worker"

        enabled = bool(start)
        camera_worker.set_head_tracking_enabled(enabled)
        return "Head tracking started" if enabled else "Head tracking stopped"


class CameraTool(ReachyRuntimeTool):
    """Capture one camera frame and optionally ask a local vision processor about it."""

    @property
    def name(self) -> str:
        return "camera"

    @property
    def description(self) -> str:
        return (
            "Capture the latest camera frame. "
            "If a local vision processor is available, ask it a question about the image."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Question to ask about the latest camera image.",
                },
            },
            "required": ["question"],
        }

    async def execute(self, question: str, **kwargs: Any) -> dict[str, Any] | str:
        _ = kwargs
        resolved_question = str(question or "").strip()
        if not resolved_question:
            return "Error: question must be a non-empty string"

        camera_worker = self.context.camera_worker
        if camera_worker is None or not hasattr(camera_worker, "get_latest_frame"):
            return "Error: camera requires a configured camera_worker"

        frame = camera_worker.get_latest_frame()
        if frame is None:
            return "Error: No frame available"

        vision_processor = self.context.vision_processor
        if vision_processor is not None and hasattr(vision_processor, "process_image"):
            try:
                result = await asyncio.to_thread(
                    vision_processor.process_image,
                    frame,
                    resolved_question,
                )
            except Exception as exc:
                return f"Error: vision processing failed: {type(exc).__name__}: {exc}"
            if isinstance(result, str):
                return {"image_description": result}
            return "Error: vision returned non-string output"

        try:
            import cv2
        except ImportError:
            return "Error: camera JPEG encoding requires cv2"

        success, buffer = cv2.imencode(".jpg", frame)
        if not success:
            return "Error: Failed to encode frame as JPEG"

        return {"b64_im": base64.b64encode(buffer.tobytes()).decode("utf-8")}


class MoveHeadTool(ReachyRuntimeTool):
    """Move the robot head toward a simple named direction."""

    _DELTAS: dict[str, tuple[int, int, int, int, int, int]] = {
        "left": (0, 0, 0, 0, 0, 40),
        "right": (0, 0, 0, 0, 0, -40),
        "up": (0, 0, 0, 0, -30, 0),
        "down": (0, 0, 0, 0, 30, 0),
        "front": (0, 0, 0, 0, 0, 0),
    }

    @property
    def name(self) -> str:
        return "move_head"

    @property
    def description(self) -> str:
        return "Move the head in one direction: left, right, up, down, or front."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["left", "right", "up", "down", "front"],
                    "description": "Direction to look toward.",
                },
            },
            "required": ["direction"],
        }

    async def execute(self, direction: str, **kwargs: Any) -> str:
        _ = kwargs
        coordinated = await self._call_coordinator("move_head", direction)
        if coordinated is not None:
            return str(coordinated)

        reachy_mini = self.context.reachy_mini
        movement_manager = self.context.movement_manager
        if reachy_mini is None or movement_manager is None:
            return "Error: move_head requires a connected ReachyMini runtime"

        normalized_direction = str(direction or "").strip().lower()
        deltas = self._DELTAS.get(normalized_direction)
        if deltas is None:
            return f"Error: Unknown direction '{direction}'"

        target = create_head_pose(*deltas, degrees=True)
        duration = max(float(self.context.motion_duration_s or 1.0), 0.1)

        try:
            current_head_pose = await asyncio.to_thread(reachy_mini.get_current_head_pose)
            current_head_joints, current_antennas = await asyncio.to_thread(
                reachy_mini.get_current_joint_positions
            )
            start_body_yaw = float(current_head_joints[0]) if current_head_joints else 0.0
            goto_move = GotoQueueMove(
                target_head_pose=target,
                start_head_pose=current_head_pose,
                target_antennas=(0.0, 0.0),
                start_antennas=(
                    float(current_antennas[0]),
                    float(current_antennas[1]),
                ),
                target_body_yaw=0.0,
                start_body_yaw=start_body_yaw,
                duration=duration,
            )
            movement_manager.queue_move(goto_move)
            movement_manager.set_moving_state(duration)
        except Exception as exc:
            return f"Error: move_head failed: {type(exc).__name__}: {exc}"

        return f"Moved head {normalized_direction}"


class PlayEmotionTool(ReachyRuntimeTool):
    """Play one recorded emotion from the bundled emotion library."""

    @property
    def name(self) -> str:
        return "play_emotion"

    @property
    def description(self) -> str:
        return "Play a recorded emotion from the Reachy Mini emotions library."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "emotion": {
                    "type": "string",
                    "description": "Emotion name from the Reachy Mini emotions library.",
                },
            },
            "required": ["emotion"],
        }

    async def execute(self, emotion: str, **kwargs: Any) -> str:
        _ = kwargs
        emotion_name = str(emotion or "").strip()
        if not emotion_name:
            return "Error: Emotion name is required"

        try:
            library = _load_recorded_moves(EMOTIONS_DATASET)
            available = library.list_moves()
            if emotion_name not in available:
                return f"Error: Unknown emotion '{emotion_name}'. Available: {available}"

            coordinated = await self._call_coordinator("play_emotion", emotion_name, library)
            if coordinated is not None:
                return str(coordinated)

            movement_manager = self.context.movement_manager
            if movement_manager is None:
                return "Error: play_emotion requires a connected ReachyMini runtime"
            movement_manager.queue_move(EmotionQueueMove(emotion_name, library))
        except Exception as exc:
            return f"Error: play_emotion failed: {type(exc).__name__}: {exc}"

        return f"Playing emotion {emotion_name}"


class DanceTool(ReachyRuntimeTool):
    """Play one or more recorded dance moves from the bundled dances library."""

    @property
    def name(self) -> str:
        return "dance"

    @property
    def description(self) -> str:
        return "Play a named or random dance move from the Reachy Mini dances library."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "move": {
                    "type": "string",
                    "description": "Dance move name, or 'random' to choose one automatically.",
                },
                "repeat": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "How many times to repeat the selected dance move.",
                },
            },
            "required": [],
        }

    async def execute(
        self,
        move: str = "random",
        repeat: int = 1,
        **kwargs: Any,
    ) -> str:
        _ = kwargs
        try:
            library = _load_recorded_moves(DANCES_DATASET)
            available = library.list_moves()
            if not available:
                return "Error: No dance moves are available"

            move_name = str(move or "random").strip()
            if not move_name or move_name == "random":
                import random

                move_name = random.choice(available)
            if move_name not in available:
                return f"Error: Unknown dance move '{move_name}'. Available: {available}"

            repeat_count = max(1, int(repeat))
            coordinated = await self._call_coordinator("dance", move_name, repeat_count, library)
            if coordinated is not None:
                return str(coordinated)

            movement_manager = self.context.movement_manager
            if movement_manager is None:
                return "Error: dance requires a connected ReachyMini runtime"
            for _ in range(repeat_count):
                movement_manager.queue_move(DanceQueueMove(move_name, library))
        except Exception as exc:
            return f"Error: dance failed: {type(exc).__name__}: {exc}"

        return f"Playing dance {move_name} x{repeat_count}"


class StopDanceTool(ReachyRuntimeTool):
    """Stop the currently playing dance or queued dance moves."""

    @property
    def name(self) -> str:
        return "stop_dance"

    @property
    def description(self) -> str:
        return "Stop the currently playing dance."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        _ = kwargs
        coordinated = await self._call_coordinator("clear_motion_queue", label="dance")
        if coordinated is not None:
            return str(coordinated)

        movement_manager = self.context.movement_manager
        if movement_manager is None:
            return "Error: stop_dance requires a connected ReachyMini runtime"
        movement_manager.clear_move_queue()
        return "Stopped dance and cleared queue"


class StopEmotionTool(ReachyRuntimeTool):
    """Stop the currently playing emotion or queued emotion moves."""

    @property
    def name(self) -> str:
        return "stop_emotion"

    @property
    def description(self) -> str:
        return "Stop the currently playing emotion."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        _ = kwargs
        coordinated = await self._call_coordinator("clear_motion_queue", label="emotion")
        if coordinated is not None:
            return str(coordinated)

        movement_manager = self.context.movement_manager
        if movement_manager is None:
            return "Error: stop_emotion requires a connected ReachyMini runtime"
        movement_manager.clear_move_queue()
        return "Stopped emotion and cleared queue"


__all__ = [
    "CameraTool",
    "DanceTool",
    "DoNothingTool",
    "HeadTrackingTool",
    "MoveHeadTool",
    "PlayEmotionTool",
    "ReachyToolContext",
    "StopDanceTool",
    "StopEmotionTool",
]
