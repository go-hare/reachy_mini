"""Built-in ``play_emotion`` action."""

from __future__ import annotations

from typing import Any

from reachy_mini.action_runtime import ActionContext, ActionMetadata, ActionSpec

from .common import BaseRobotAction, call_sdk, seconds

DEFAULT_LIBRARY = "pollen-robotics/reachy-mini-emotions-library"

PARAMETER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "library": {"type": "string"},
        "initial_goto_duration_s": {"type": "number", "minimum": 0.0, "maximum": 5.0},
        "speed": {"type": "number", "minimum": 0.5, "maximum": 2.0},
    },
}


METADATA = ActionMetadata(
    name="play_emotion",
    description="Play a named expressive emotion from the recorded moves library.",
    parameter_schema=PARAMETER_SCHEMA,
    required_locks=frozenset({"head", "body_yaw", "antenna_left", "antenna_right"}),
    default_priority=40,
    default_interruptible=True,
    default_duration_s=None,
    tags=["emotion", "recorded_move"],
)


class PlayEmotionAction(BaseRobotAction):
    """Play a recorded expressive movement."""

    async def run(self, context: ActionContext) -> None:
        """Load and play the requested recorded move."""
        from reachy_mini.motion.recorded_move import RecordedMoves

        move_name = str(self.spec.params["name"])
        library = str(self.spec.params.get("library", DEFAULT_LIBRARY))
        initial_goto = seconds(self.spec.params.get("initial_goto_duration_s"), 1.0)
        speed = float(self.spec.params.get("speed", 1.0))
        await context.cancel_token.checkpoint()
        moves = await call_sdk(RecordedMoves, library)
        move = await call_sdk(moves.get, move_name)
        if hasattr(context.mini, "async_play_move"):
            await context.mini.async_play_move(
                move,
                initial_goto_duration=initial_goto,
                speed=speed,
            )
        elif hasattr(context.mini, "play_move"):
            await call_sdk(
                context.mini.play_move,
                move,
                initial_goto_duration=initial_goto,
                speed=speed,
            )
        else:
            await call_sdk(context.mini.goto_target, duration=initial_goto)


def build(spec: ActionSpec) -> PlayEmotionAction:
    """Build a play-emotion robot action."""
    return PlayEmotionAction(
        spec,
        required_locks={"head", "body_yaw", "antenna_left", "antenna_right"},
        duration_s=None,
        priority=METADATA.default_priority,
        interruptible=METADATA.default_interruptible,
    )
