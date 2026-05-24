"""Built-in ``nod`` action."""

from __future__ import annotations

from typing import Any

from reachy_mini.action_runtime import ActionContext, ActionMetadata, ActionSpec

from .common import (
    BaseRobotAction,
    call_sdk,
    checkpoint_sleep,
    pose_from_euler,
    seconds,
)

PARAMETER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "cycles": {"type": "integer", "minimum": 1, "maximum": 5},
        "amplitude_deg": {"type": "number", "minimum": 3, "maximum": 25},
        "period_s": {"type": "number", "minimum": 0.3, "maximum": 1.5},
    },
}


METADATA = ActionMetadata(
    name="nod",
    description="Make a short affirmative head nod.",
    parameter_schema=PARAMETER_SCHEMA,
    required_locks=frozenset({"head"}),
    default_priority=40,
    default_interruptible=True,
    default_duration_s=1.2,
    tags=["head", "gesture"],
)


class NodAction(BaseRobotAction):
    """Run a pitch-based nod sequence."""

    async def run(self, context: ActionContext) -> None:
        """Execute the nod sequence."""
        cycles = int(self.spec.params.get("cycles", 2))
        amplitude = float(self.spec.params.get("amplitude_deg", 12))
        period = seconds(self.spec.params.get("period_s"), 0.6)
        half_period = period / 2.0
        for _ in range(cycles):
            await context.cancel_token.checkpoint()
            await call_sdk(
                context.mini.goto_target,
                head=pose_from_euler(pitch_deg=amplitude),
                duration=half_period,
            )
            await checkpoint_sleep(context, 0)
            await call_sdk(
                context.mini.goto_target,
                head=pose_from_euler(pitch_deg=-amplitude),
                duration=half_period,
            )
        await context.cancel_token.checkpoint()
        await call_sdk(context.mini.goto_target, head=pose_from_euler(), duration=half_period)


def build(spec: ActionSpec) -> NodAction:
    """Build a nod robot action."""
    cycles = int(spec.params.get("cycles", 2))
    period = seconds(spec.params.get("period_s"), 0.6)
    return NodAction(
        spec,
        required_locks={"head"},
        duration_s=(cycles * period) + (period / 2.0),
        priority=METADATA.default_priority,
        interruptible=METADATA.default_interruptible,
    )
