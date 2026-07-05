"""Social expression planning policy."""

from __future__ import annotations

from dataclasses import dataclass

from reachy_mini.robot_runtime.contracts import IntentType
from reachy_mini.robot_runtime.policy import (
    PolicyContext,
    PolicyResult,
    build_single_step_plan,
)

_SUPPORTED = {
    IntentType.GREET,
    IntentType.ACKNOWLEDGE,
    IntentType.AGREE,
    IntentType.REFUSE,
}


@dataclass(frozen=True, slots=True)
class SocialPolicy:
    """Compile social intents without referencing body-specific assets."""

    name: str = "social_policy"

    def supports(self, context: PolicyContext) -> bool:
        """Return true for social expression intents."""
        return context.intent.intent_type in _SUPPORTED

    def build_plan(self, context: PolicyContext) -> PolicyResult:
        """Build a gesture-oriented social behavior plan."""
        intent_type = context.intent.intent_type.value
        plan = build_single_step_plan(
            context,
            policy_name=self.name,
            default_channels=("gesture",),
            semantic_tags=(intent_type,),
        )
        return PolicyResult(
            policy_name=self.name,
            plan=plan,
            reasons=(f"{intent_type}_to_semantic_gesture",),
        )


__all__ = ["SocialPolicy"]
