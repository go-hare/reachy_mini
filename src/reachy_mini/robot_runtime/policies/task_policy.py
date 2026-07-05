"""Task execution planning policy."""

from __future__ import annotations

from dataclasses import dataclass

from reachy_mini.robot_runtime.contracts import IntentType, TimingAnchor
from reachy_mini.robot_runtime.policy import (
    PolicyContext,
    PolicyResult,
    build_single_step_plan,
)


@dataclass(frozen=True, slots=True)
class TaskPolicy:
    """Compile long-running task requests into task-start plans."""

    name: str = "task_policy"

    def supports(self, context: PolicyContext) -> bool:
        """Return true for task execution intents."""
        return context.intent.intent_type is IntentType.TASK_EXECUTE

    def build_plan(self, context: PolicyContext) -> PolicyResult:
        """Build a task-oriented behavior plan."""
        constraints = context.constraints
        constraints.setdefault("timing_anchor", TimingAnchor.TASK_START.value)
        patched = PolicyContext(
            intent=context.intent.__class__(
                **{**context.intent.to_dict(), "constraints": constraints}
            ),
            robot_state=context.robot_state,
        )
        plan = build_single_step_plan(
            patched,
            policy_name=self.name,
            default_channels=("navigation",),
            semantic_tags=("task_execute",),
            command_type="execute_task",
        )
        return PolicyResult(
            policy_name=self.name,
            plan=plan,
            reasons=("task_execute_to_task_start",),
        )


__all__ = ["TaskPolicy"]
