"""Built-in ``shake_head`` action."""

from __future__ import annotations

from typing import Any

from reachy_mini.action_runtime import ActionContext, ActionMetadata, ActionSpec

from .common import BaseRobotAction, call_sdk, pose_from_euler, seconds

PARAMETER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "cycles": {"type": "integer", "minimum": 1, "maximum": 5},
        "amplitude_deg": {"type": "number", "minimum": 3, "maximum": 35},
        "period_s": {"type": "number", "minimum": 0.3, "maximum": 1.5},
    },
}


METADATA = ActionMetadata(
    name="shake_head",
    description="Make a short negative head shake.",
    parameter_schema=PARAMETER_SCHEMA,
    required_locks=frozenset({"head"}),
    default_priority=40,
    default_interruptible=True,
    default_duration_s=1.4,
    tags=["head", "gesture"],
)


class ShakeHeadAction(BaseRobotAction):
    """Run a yaw-based head shake sequence."""

    async def run(self, context: ActionContext) -> None:
        """Execute the shake sequence."""
        cycles = int(self.spec.params.get("cycles", 2))
        amplitude = float(self.spec.params.get("amplitude_deg", 18))
        period = seconds(self.spec.params.get("period_s"), 0.7)
        half_period = period / 2.0
        for _ in range(cycles):
            await context.cancel_token.checkpoint()
            await call_sdk(
                context.mini.goto_target,
                head=pose_from_euler(yaw_deg=amplitude),
                duration=half_period,
            )
            await context.cancel_token.checkpoint()
            await call_sdk(
                context.mini.goto_target,
                head=pose_from_euler(yaw_deg=-amplitude),
                duration=half_period,
            )
        await context.cancel_token.checkpoint()
        await call_sdk(context.mini.goto_target, head=pose_from_euler(), duration=half_period)


def build(spec: ActionSpec) -> ShakeHeadAction:
    """Build a shake-head robot action."""
    cycles = int(spec.params.get("cycles", 2))
    period = seconds(spec.params.get("period_s"), 0.7)
    return ShakeHeadAction(
        spec,
        required_locks={"head"},
        duration_s=(cycles * period) + (period / 2.0),
        priority=METADATA.default_priority,
        interruptible=METADATA.default_interruptible,
    )
