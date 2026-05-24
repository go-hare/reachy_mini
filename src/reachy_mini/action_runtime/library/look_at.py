"""Built-in ``look_at`` action."""

from __future__ import annotations

from typing import Any

from reachy_mini.action_runtime import ActionContext, ActionMetadata, ActionSpec

from .common import (
    INTERPOLATION_VALUES,
    BaseRobotAction,
    call_sdk,
    interpolation_from_param,
    seconds,
)

PARAMETER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["target"],
    "additionalProperties": False,
    "properties": {
        "target": {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 3,
            "maxItems": 3,
        },
        "duration_s": {"type": "number", "minimum": 0.2, "maximum": 5.0},
        "interpolation": {"type": "string", "enum": INTERPOLATION_VALUES},
        "keep_after": {"type": "boolean"},
    },
}


METADATA = ActionMetadata(
    name="look_at",
    description="Turn the robot head toward a point in front of it.",
    parameter_schema=PARAMETER_SCHEMA,
    required_locks=frozenset({"head"}),
    default_priority=40,
    default_interruptible=True,
    default_duration_s=0.8,
    tags=["head", "gesture"],
)


class LookAtAction(BaseRobotAction):
    """Point the head toward a 3D target."""

    async def run(self, context: ActionContext) -> None:
        """Execute the look-at movement through the SDK helper."""
        target = [float(item) for item in self.spec.params["target"]]
        duration = seconds(self.spec.params.get("duration_s"), 0.8)
        interpolation = interpolation_from_param(
            str(self.spec.params.get("interpolation", "minjerk"))
        )
        await context.cancel_token.checkpoint()
        if hasattr(context.mini, "look_at_world"):
            await call_sdk(
                context.mini.look_at_world,
                target[0],
                target[1],
                target[2],
                duration,
                True,
            )
        else:
            await call_sdk(
                context.mini.goto_target,
                head={"look_at": target},
                duration=duration,
                method=interpolation,
            )


def build(spec: ActionSpec) -> LookAtAction:
    """Build a look-at robot action."""
    duration = seconds(spec.params.get("duration_s"), 0.8)
    return LookAtAction(
        spec,
        required_locks={"head"},
        duration_s=duration,
        priority=METADATA.default_priority,
        interruptible=METADATA.default_interruptible,
    )
