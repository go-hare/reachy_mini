"""Attention and gaze planning policy."""

from __future__ import annotations

from dataclasses import dataclass

from reachy_mini.robot_runtime.contracts import IntentType
from reachy_mini.robot_runtime.policy import (
    PolicyContext,
    PolicyResult,
    build_single_step_plan,
)


@dataclass(frozen=True, slots=True)
class AttentionPolicy:
    """Compile attention-shift intents into gaze/head plans."""

    name: str = "attention_policy"

    def supports(self, context: PolicyContext) -> bool:
        """Return true for attention-shift intents."""
        return context.intent.intent_type is IntentType.ATTENTION_SHIFT

    def build_plan(self, context: PolicyContext) -> PolicyResult:
        """Build a gaze-oriented behavior plan."""
        plan = build_single_step_plan(
            context,
            policy_name=self.name,
            default_channels=("gaze", "head"),
            semantic_tags=("attention_shift", "look"),
            command_type="set_gaze",
        )
        return PolicyResult(
            policy_name=self.name,
            plan=plan,
            reasons=("attention_shift_to_gaze",),
        )


__all__ = ["AttentionPolicy"]
