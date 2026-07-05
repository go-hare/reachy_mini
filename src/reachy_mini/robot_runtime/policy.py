"""Policy interfaces for compiling embodied intents into behavior plans."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from reachy_mini.robot_runtime.bridges.policy_service import PolicyServiceBackend
from reachy_mini.robot_runtime.contracts import (
    BehaviorPlan,
    BehaviorStep,
    EmbodiedIntent,
    FallbackPolicy,
    RobotState,
    TimingAnchor,
)


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """Read-only planning context passed to intent policies."""

    intent: EmbodiedIntent
    robot_state: RobotState | None = None

    @property
    def constraints(self) -> dict[str, object]:
        """Return a mutable copy of intent constraints."""
        return dict(self.intent.constraints)


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """Result returned by a planning policy."""

    policy_name: str
    plan: BehaviorPlan
    reasons: tuple[str, ...] = ()
    confidence: float = 1.0


class PlanPolicy(Protocol):
    """Protocol implemented by all behavior planning policies."""

    name: str

    def supports(self, context: PolicyContext) -> bool:
        """Return whether this policy can plan for the intent."""

    def build_plan(self, context: PolicyContext) -> PolicyResult:
        """Compile one embodied intent into a behavior plan."""


@dataclass(frozen=True, slots=True)
class RobotPolicyEngine:
    """Ordered policy chain used by RobotRuntime System 1."""

    policies: tuple[PlanPolicy, ...]

    def plan_intent(
        self,
        intent: EmbodiedIntent,
        *,
        robot_state: RobotState | None = None,
    ) -> PolicyResult:
        """Select the first matching policy and return its plan."""
        context = PolicyContext(intent=intent, robot_state=robot_state)
        for policy in self.policies:
            if policy.supports(context):
                return policy.build_plan(context)
        raise ValueError(f"No RobotRuntime policy supports intent_type={intent.intent_type.value}")


def create_default_policy_engine(
    *,
    policy_backend: PolicyServiceBackend | None = None,
) -> RobotPolicyEngine:
    """Create the default enterprise policy chain."""
    from reachy_mini.robot_runtime.policies import (
        AttentionPolicy,
        IdlePolicy,
        LearnedPolicy,
        SocialPolicy,
        SpeechSyncPolicy,
        TaskPolicy,
    )

    learned = (LearnedPolicy(policy_backend),) if policy_backend is not None else ()
    return RobotPolicyEngine(
        policies=(
            *learned,
            TaskPolicy(),
            AttentionPolicy(),
            SpeechSyncPolicy(),
            SocialPolicy(),
            IdlePolicy(),
        )
    )


def build_single_step_plan(
    context: PolicyContext,
    *,
    policy_name: str,
    default_channels: Sequence[str],
    semantic_tags: Sequence[str] | None = None,
    command_type: str | None = None,
    fallback_policy: FallbackPolicy | str | None = None,
) -> BehaviorPlan:
    """Build the common one-step plan shape used by default policies."""
    intent = context.intent
    constraints = context.constraints
    channels = _channels_from_constraints(intent, constraints, default_channels)
    timing_anchor = _timing_anchor(intent, constraints)
    fallback = _fallback_policy(constraints, fallback_policy)
    plan = BehaviorPlan(
        intent_id=intent.intent_id,
        priority=intent.priority,
        timing_anchor=timing_anchor,
        start_after_ms=_optional_int(constraints, "start_after_ms"),
        deadline_ms=_optional_int(constraints, "deadline_ms"),
        duration_ms=_optional_int(constraints, "duration_ms"),
        interruptible=_bool_constraint(constraints, "interruptible", default=True),
        channels=channels,
        constraints={
            **constraints,
            "policy_name": policy_name,
            "behavior_node": behavior_node_for_policy(policy_name),
        },
        fallback_policy=fallback,
    )
    step = BehaviorStep(
        channel=channels[0],
        plan_id=plan.plan_id,
        timing={"anchor": timing_anchor.value},
        capability_query=_capability_query(
            context,
            policy_name=policy_name,
            channels=channels,
            semantic_tags=semantic_tags,
            command_type=command_type,
        ),
        timeout_ms=int(constraints.get("timeout_ms", 1000)),
    )
    return replace(plan, steps=[step])


def _capability_query(
    context: PolicyContext,
    *,
    policy_name: str,
    channels: list[str],
    semantic_tags: Sequence[str] | None,
    command_type: str | None,
) -> dict[str, object]:
    intent = context.intent
    constraints = context.constraints
    query: dict[str, object] = {
        "semantic_tags": list(semantic_tags or (intent.intent_type.value,)),
        "channels": channels,
        "affect": intent.affect,
        "intensity": intent.intensity,
        "adapter_id": constraints.get("adapter_id"),
        "constraints": constraints,
        "payload": {
            "turn_id": intent.turn_id,
            "target": intent.target,
            "constraints": constraints,
            "policy_name": policy_name,
            "behavior_node": behavior_node_for_policy(policy_name),
        },
    }
    if command_type:
        query["command_type"] = command_type
    return query


def _channels_from_constraints(
    intent: EmbodiedIntent,
    constraints: dict[str, object],
    default_channels: Sequence[str],
) -> list[str]:
    explicit = constraints.get("channels") or constraints.get("modalities")
    channels = _string_list(explicit) or list(intent.modalities) or list(default_channels)
    return channels


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value]
    return [str(value)]


def _timing_anchor(
    intent: EmbodiedIntent,
    constraints: dict[str, object],
) -> TimingAnchor:
    value = constraints.get("timing_anchor") or intent.timing_anchor or TimingAnchor.IDLE
    return value if isinstance(value, TimingAnchor) else TimingAnchor(str(value))


def _optional_int(constraints: dict[str, object], key: str) -> int | None:
    value = constraints.get(key)
    return int(value) if value is not None else None


def _bool_constraint(
    constraints: dict[str, object],
    key: str,
    *,
    default: bool,
) -> bool:
    value = constraints.get(key)
    if value is None:
        return default
    if isinstance(value, str):
        return value.lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _fallback_policy(
    constraints: dict[str, object],
    fallback_policy: FallbackPolicy | str | None,
) -> FallbackPolicy:
    value = constraints.get("fallback_policy") or fallback_policy or FallbackPolicy.SAFE_IDLE
    return value if isinstance(value, FallbackPolicy) else FallbackPolicy(str(value))


def behavior_node_for_policy(policy_name: str) -> str:
    """Return the stable System 1 behavior node name for a planning policy."""
    return f"RobotPolicyEngine/{policy_name}"


__all__ = [
    "PlanPolicy",
    "PolicyContext",
    "PolicyResult",
    "RobotPolicyEngine",
    "behavior_node_for_policy",
    "build_single_step_plan",
    "create_default_policy_engine",
]
