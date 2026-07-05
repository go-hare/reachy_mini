"""Resolve behavior steps into adapter commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from reachy_mini.robot_runtime.capabilities import (
    CapabilityMatch,
    CapabilityQuery,
    CapabilityRegistry,
)
from reachy_mini.robot_runtime.contracts import BehaviorPlan, BehaviorStep, RobotCommand


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    """Result of resolving one behavior step."""

    status: str
    plan_id: str
    step_id: str
    command: RobotCommand | None = None
    match: CapabilityMatch | None = None
    reasons: tuple[str, ...] = ()


@dataclass(slots=True)
class CapabilityResolver:
    """Converts body-agnostic behavior steps to adapter commands."""

    registry: CapabilityRegistry

    def resolve_step(self, plan: BehaviorPlan, step: BehaviorStep) -> ResolutionResult:
        """Resolve one step to one best-match command."""
        query = _query_from_step(step)
        matches = self.registry.query(query)
        if not matches:
            return ResolutionResult(
                status="unresolved",
                plan_id=plan.plan_id,
                step_id=step.step_id,
                reasons=("no_capability_match",),
            )
        match = matches[0]
        command = _command_from_match(plan, step, match)
        return ResolutionResult(
            status="resolved",
            plan_id=plan.plan_id,
            step_id=step.step_id,
            command=command,
            match=match,
            reasons=match.reasons,
        )

    def resolve_plan(self, plan: BehaviorPlan) -> list[ResolutionResult]:
        """Resolve every step in a plan."""
        return [self.resolve_step(plan, step) for step in plan.steps]


def _query_from_step(step: BehaviorStep) -> CapabilityQuery:
    raw = dict(step.capability_query)
    channels = tuple(raw.get("channels") or (step.channel,))
    raw["channels"] = channels
    return CapabilityQuery.from_dict(raw)


def _command_from_match(
    plan: BehaviorPlan,
    step: BehaviorStep,
    match: CapabilityMatch,
) -> RobotCommand:
    capability = match.capability
    command_type = str(
        step.capability_query.get("command_type")
        or capability.input_schema.get("command_type")
        or _default_command_type(capability.modality)
    )
    payload = _payload_from_step(step.capability_query)
    return RobotCommand(
        plan_id=plan.plan_id,
        step_id=step.step_id,
        adapter_id=capability.adapter_id,
        capability_id=capability.capability_id,
        command_type=command_type,
        payload=payload,
        duration_ms=plan.duration_ms,
        timeout_ms=step.timeout_ms,
    )


def _payload_from_step(query: dict[str, Any]) -> dict[str, Any]:
    payload = dict(query.get("payload") or {})
    for key in ("semantic_tags", "affect", "intensity"):
        if key in query and key not in payload:
            payload[key] = query[key]
    return payload


def _default_command_type(modality: str) -> str:
    defaults = {
        "expression": "apply_expression",
        "motion": "play_motion",
        "gaze": "set_gaze",
        "speech_motion": "play_speech_motion",
        "navigation": "navigate",
    }
    return defaults.get(modality, "execute")


__all__ = ["CapabilityResolver", "ResolutionResult"]
