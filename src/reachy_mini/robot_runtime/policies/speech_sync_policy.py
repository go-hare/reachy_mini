"""Speech-synchronized planning policy."""

from __future__ import annotations

from dataclasses import dataclass

from reachy_mini.robot_runtime.contracts import IntentType, TimingAnchor
from reachy_mini.robot_runtime.policy import (
    PolicyContext,
    PolicyResult,
    build_single_step_plan,
)

_SUPPORTED = {IntentType.LISTEN, IntentType.THINK, IntentType.SPEAK}


@dataclass(frozen=True, slots=True)
class SpeechSyncPolicy:
    """Compile speech-phase intents into face/gesture plans."""

    name: str = "speech_sync_policy"

    def supports(self, context: PolicyContext) -> bool:
        """Return true for speech lifecycle intents."""
        return context.intent.intent_type in _SUPPORTED

    def build_plan(self, context: PolicyContext) -> PolicyResult:
        """Build a plan aligned to the speech lifecycle."""
        constraints = context.constraints
        if "timing_anchor" not in constraints:
            constraints["timing_anchor"] = _default_anchor(context.intent.intent_type).value
        patched = PolicyContext(
            intent=context.intent.__class__(
                **{**context.intent.to_dict(), "constraints": constraints}
            ),
            robot_state=context.robot_state,
        )
        plan = build_single_step_plan(
            patched,
            policy_name=self.name,
            default_channels=("face",),
            semantic_tags=(context.intent.intent_type.value,),
        )
        return PolicyResult(
            policy_name=self.name,
            plan=plan,
            reasons=("speech_lifecycle_timing_anchor",),
        )


def _default_anchor(intent_type: IntentType) -> TimingAnchor:
    if intent_type is IntentType.LISTEN:
        return TimingAnchor.TURN_START
    if intent_type is IntentType.THINK:
        return TimingAnchor.SPEECH_PREPARE
    return TimingAnchor.SPEECH_START


__all__ = ["SpeechSyncPolicy"]
