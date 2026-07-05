"""Idle and recovery planning policy."""

from __future__ import annotations

from dataclasses import dataclass

from reachy_mini.robot_runtime.contracts import IntentType
from reachy_mini.robot_runtime.policy import (
    PolicyContext,
    PolicyResult,
    build_single_step_plan,
)


@dataclass(frozen=True, slots=True)
class IdlePolicy:
    """Fallback policy for safe idle and recovery intents."""

    name: str = "idle_policy"

    def supports(self, context: PolicyContext) -> bool:
        """Return true for idle, recover, or unsupported fallback intents."""
        return context.intent.intent_type in {IntentType.IDLE, IntentType.RECOVER}

    def build_plan(self, context: PolicyContext) -> PolicyResult:
        """Build a low-intensity idle behavior plan."""
        plan = build_single_step_plan(
            context,
            policy_name=self.name,
            default_channels=("face",),
            semantic_tags=(context.intent.intent_type.value, "idle"),
        )
        return PolicyResult(
            policy_name=self.name,
            plan=plan,
            reasons=("safe_idle_or_recover",),
            confidence=0.8,
        )


__all__ = ["IdlePolicy"]
