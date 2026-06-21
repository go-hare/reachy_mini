"""Built-in ``play_emotion`` action."""

from __future__ import annotations

from typing import Any

from reachy_mini.action_runtime import ActionContext, ActionMetadata, ActionSpec

from .common import BaseRobotAction, call_sdk, seconds

DEFAULT_LIBRARY = "pollen-robotics/reachy-mini-emotions-library"

EMOTION_ALIASES: dict[str, tuple[str, ...]] = {
    "happy": ("cheerful1", "success1", "enthusiastic1", "proud1"),
    "excited": ("enthusiastic1", "enthusiastic2", "no_excited1", "success1"),
    "joy": ("cheerful1", "success1", "enthusiastic1"),
    "joyful": ("cheerful1", "success1", "enthusiastic1"),
    "glad": ("cheerful1", "success1"),
    "sad": ("sad1", "sad2", "downcast1", "lonely1"),
    "angry": ("furious1", "rage1", "irritated1"),
    "mad": ("furious1", "rage1", "irritated1"),
    "surprised": ("surprised1", "surprised2", "amazed1"),
    "scared": ("scared1", "fear1", "anxiety1"),
    "fear": ("fear1", "scared1", "anxiety1"),
    "confused": ("confused1", "uncertain1", "incomprehensible2"),
    "curious": ("curious1", "inquiring1", "inquiring2"),
    "tired": ("tired1", "exhausted1", "sleep1"),
    "sleepy": ("sleep1", "tired1", "exhausted1"),
    "calm": ("calming1", "serenity1", "relief1"),
    "yes": ("yes1", "yes_sad1"),
    "no": ("no1", "no_excited1", "no_sad1"),
}

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
        move_name = resolve_emotion_move_name(move_name, moves.list_moves())
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


def resolve_emotion_move_name(requested_name: str, available_moves: list[str]) -> str:
    """Map semantic emotion labels to concrete recorded-move names."""

    requested = str(requested_name or "").strip()
    available = list(available_moves)
    if requested in available:
        return requested

    normalized = requested.lower().replace("-", "_").replace(" ", "_")
    available_by_lower = {name.lower(): name for name in available}
    if normalized in available_by_lower:
        return available_by_lower[normalized]

    for alias in EMOTION_ALIASES.get(normalized, ()):
        if alias in available:
            return alias

    for candidate in available:
        lowered = candidate.lower()
        if lowered.startswith(f"{normalized}_") or lowered.startswith(normalized):
            return candidate

    for candidate in available:
        if normalized and normalized in candidate.lower():
            return candidate

    raise ValueError(
        f"Move {requested_name} not found in recorded moves library. "
        f"Available moves: {available}"
    )


def build(spec: ActionSpec) -> PlayEmotionAction:
    """Build a play-emotion robot action."""
    return PlayEmotionAction(
        spec,
        required_locks={"head", "body_yaw", "antenna_left", "antenna_right"},
        duration_s=None,
        priority=METADATA.default_priority,
        interruptible=METADATA.default_interruptible,
    )
