"""Built-in ``set_antenna`` action."""

from __future__ import annotations

from typing import Any

from reachy_mini.action_runtime import (
    ActionContext,
    ActionMetadata,
    ActionParamError,
    ActionSpec,
)

from .common import BaseRobotAction, call_sdk, degrees_to_radians, seconds

ANGLE_SCHEMA: dict[str, Any] = {
    "anyOf": [
        {"type": "number", "minimum": -180, "maximum": 180},
        {"type": "null"},
    ]
}

PARAMETER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "left_deg": ANGLE_SCHEMA,
        "right_deg": ANGLE_SCHEMA,
        "duration_s": {"type": "number", "minimum": 0.1, "maximum": 5.0},
    },
}


METADATA = ActionMetadata(
    name="set_antenna",
    description="Move one or both antennas to a visible position.",
    parameter_schema=PARAMETER_SCHEMA,
    required_locks=frozenset({"antenna_left", "antenna_right"}),
    default_priority=40,
    default_interruptible=True,
    default_duration_s=0.5,
    tags=["antenna", "gesture"],
)


class SetAntennaAction(BaseRobotAction):
    """Move antenna targets."""

    async def prepare(self, context: ActionContext) -> None:
        """Require at least one antenna target."""
        if self.spec.params.get("left_deg") is None and self.spec.params.get("right_deg") is None:
            raise ActionParamError("set_antenna requires left_deg or right_deg.")

    async def run(self, context: ActionContext) -> None:
        """Execute the antenna movement."""
        duration = seconds(self.spec.params.get("duration_s"), 0.5)
        current = [0.0, 0.0]
        if hasattr(context.mini, "get_present_antenna_joint_positions"):
            current = list(await call_sdk(context.mini.get_present_antenna_joint_positions))
        right = (
            degrees_to_radians(float(self.spec.params["right_deg"]))
            if self.spec.params.get("right_deg") is not None
            else current[0]
        )
        left = (
            degrees_to_radians(float(self.spec.params["left_deg"]))
            if self.spec.params.get("left_deg") is not None
            else current[1]
        )
        await context.cancel_token.checkpoint()
        await call_sdk(context.mini.goto_target, antennas=[right, left], duration=duration)


def build(spec: ActionSpec) -> SetAntennaAction:
    """Build a set-antenna robot action with narrowed locks."""
    locks: set[str] = set()
    if spec.params.get("left_deg") is not None:
        locks.add("antenna_left")
    if spec.params.get("right_deg") is not None:
        locks.add("antenna_right")
    if not locks:
        locks = {"antenna_left", "antenna_right"}
    return SetAntennaAction(
        spec,
        required_locks=locks,
        duration_s=seconds(spec.params.get("duration_s"), 0.5),
        priority=METADATA.default_priority,
        interruptible=METADATA.default_interruptible,
    )
