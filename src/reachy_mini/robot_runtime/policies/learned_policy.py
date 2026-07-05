"""Learned policy backend adapter for RobotRuntime planning."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from reachy_mini.robot_runtime.body_agnostic import validate_body_agnostic_value
from reachy_mini.robot_runtime.bridges.policy_service import (
    PolicyServiceBackend,
    PolicyServiceRequest,
    PolicyServiceResponse,
)
from reachy_mini.robot_runtime.contracts import (
    BehaviorPlan,
    BehaviorStep,
    FallbackPolicy,
    RobotState,
    TimingAnchor,
)
from reachy_mini.robot_runtime.policy import (
    PolicyContext,
    PolicyResult,
    behavior_node_for_policy,
    build_single_step_plan,
)


@dataclass(frozen=True, slots=True)
class LearnedPolicy:
    """Convert external policy-service actions into BehaviorPlan objects."""

    backend: PolicyServiceBackend
    name: str = "learned_policy"
    trigger_constraint: str = "policy_backend"

    def supports(self, context: PolicyContext) -> bool:
        """Return true when the intent explicitly requests learned policy planning."""
        constraints = context.constraints
        return bool(
            constraints.get(self.trigger_constraint)
            or constraints.get("use_learned_policy")
        )

    def build_plan(self, context: PolicyContext) -> PolicyResult:
        """Build a safe Runtime plan from external policy-service output."""
        response = self._call_backend(context)
        if response.status == "safe_idle" or not response.actions:
            plan = build_single_step_plan(
                context,
                policy_name=self.name,
                default_channels=("face",),
                semantic_tags=("idle",),
                fallback_policy=FallbackPolicy.SAFE_IDLE,
            )
            return PolicyResult(
                policy_name=self.name,
                plan=plan,
                reasons=(response.reason or "policy_safe_idle",),
                confidence=0.0,
            )
        plan = _plan_from_response(context, policy_name=self.name, response=response)
        return PolicyResult(
            policy_name=self.name,
            plan=plan,
            reasons=(response.reason or response.status,),
            confidence=float(context.constraints.get("policy_confidence", 1.0)),
        )

    def _call_backend(self, context: PolicyContext) -> PolicyServiceResponse:
        state = context.robot_state or RobotState()
        request = PolicyServiceRequest(
            state=state,
            intent=context.intent,
            context={
                "policy_name": self.name,
                "constraints": context.constraints,
            },
        )
        try:
            return self.backend.plan(request)
        except Exception as exc:  # pragma: no cover - defensive backend boundary
            return PolicyServiceResponse.safe_idle(
                f"{type(exc).__name__}: {exc}",
            )


def _plan_from_response(
    context: PolicyContext,
    *,
    policy_name: str,
    response: PolicyServiceResponse,
) -> BehaviorPlan:
    constraints = context.constraints
    channels = _channels_from_actions(response.actions)
    plan = BehaviorPlan(
        intent_id=context.intent.intent_id,
        priority=context.intent.priority,
        timing_anchor=_timing_anchor(context),
        start_after_ms=_optional_int(constraints, "start_after_ms"),
        deadline_ms=_optional_int(constraints, "deadline_ms"),
        duration_ms=_optional_int(constraints, "duration_ms"),
        interruptible=_bool_constraint(constraints, "interruptible", default=True),
        channels=channels,
        constraints={
            **constraints,
            "policy_name": policy_name,
            "behavior_node": behavior_node_for_policy(policy_name),
            "policy_service_status": response.status,
        },
        fallback_policy=_fallback_policy(constraints),
    )
    steps = [
        _step_from_action(
            action,
            context=context,
            policy_name=policy_name,
            plan_id=plan.plan_id,
            index=index,
        )
        for index, action in enumerate(response.actions, start=1)
    ]
    return replace(plan, steps=steps)


def _step_from_action(
    action: dict[str, Any],
    *,
    context: PolicyContext,
    policy_name: str,
    plan_id: str,
    index: int,
) -> BehaviorStep:
    _validate_policy_action(action)
    channels = _string_list(action.get("channels")) or [str(action.get("channel") or "gesture")]
    semantic_tags = _string_list(action.get("semantic_tags")) or [
        context.intent.intent_type.value
    ]
    query: dict[str, object] = {
        "semantic_tags": semantic_tags,
        "channels": channels,
        "affect": action.get("affect", context.intent.affect),
        "intensity": action.get("intensity", context.intent.intensity),
        "adapter_id": context.constraints.get("adapter_id"),
        "constraints": {**context.constraints, **dict(action.get("constraints") or {})},
        "payload": {
            "turn_id": context.intent.turn_id,
            "target": context.intent.target,
            "policy_name": policy_name,
            "behavior_node": behavior_node_for_policy(policy_name),
            "policy_action_index": index,
            **dict(action.get("payload") or {}),
        },
    }
    return BehaviorStep(
        channel=channels[0],
        plan_id=plan_id,
        timing=dict(action.get("timing") or {}),
        capability_query=query,
        timeout_ms=int(action.get("timeout_ms", context.constraints.get("timeout_ms", 1000))),
        required=bool(action.get("required", True)),
    )


def _validate_policy_action(action: dict[str, Any]) -> None:
    try:
        validate_body_agnostic_value("learned policy action", action)
    except ValueError as exc:
        raise ValueError(
            "learned policy actions must stay body-agnostic: " + str(exc)
        ) from exc


def _channels_from_actions(actions: list[dict[str, Any]]) -> list[str]:
    channels: list[str] = []
    for action in actions:
        for channel in _string_list(action.get("channels")) or [
            str(action.get("channel") or "gesture")
        ]:
            if channel not in channels:
                channels.append(channel)
    return channels or ["gesture"]


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _timing_anchor(context: PolicyContext) -> TimingAnchor:
    constraints = context.constraints
    value = constraints.get("timing_anchor") or context.intent.timing_anchor or TimingAnchor.IDLE
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


def _fallback_policy(constraints: dict[str, object]) -> FallbackPolicy:
    value = constraints.get("fallback_policy", FallbackPolicy.SAFE_IDLE.value)
    return value if isinstance(value, FallbackPolicy) else FallbackPolicy(str(value))


__all__ = ["LearnedPolicy"]
