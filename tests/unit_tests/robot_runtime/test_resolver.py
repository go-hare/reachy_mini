"""Tests for RobotRuntime capability resolver."""

from __future__ import annotations

from reachy_mini.robot_runtime.capabilities import CapabilityRegistry
from reachy_mini.robot_runtime.contracts import BehaviorPlan, BehaviorStep, Capability
from reachy_mini.robot_runtime.resolver import CapabilityResolver


def test_resolver_builds_command_from_best_capability() -> None:
    """Resolver turns a semantic step into an adapter command."""

    registry = CapabilityRegistry()
    registry.register(
        Capability(
            capability_id="live2d_greet",
            adapter_id="live2d",
            embodiment="live2d",
            modality="motion",
            channels=["gesture"],
            semantic_tags=["greet"],
            input_schema={"command_type": "play_motion"},
        )
    )
    plan = BehaviorPlan(intent_id="intent", plan_id="plan", duration_ms=700)
    step = BehaviorStep(
        step_id="step",
        plan_id="plan",
        channel="gesture",
        capability_query={
            "semantic_tags": ["greet"],
            "modality": "motion",
            "payload": {"loop": False},
        },
        timeout_ms=900,
    )

    result = CapabilityResolver(registry).resolve_step(plan, step)

    assert result.status == "resolved"
    assert result.command is not None
    assert result.command.adapter_id == "live2d"
    assert result.command.command_type == "play_motion"
    assert result.command.payload["loop"] is False
    assert result.command.payload["semantic_tags"] == ["greet"]


def test_resolver_reports_unresolved_step() -> None:
    """Missing capability is represented as a structured result."""

    plan = BehaviorPlan(intent_id="intent", plan_id="plan")
    step = BehaviorStep(channel="face", capability_query={"semantic_tags": ["greet"]})

    result = CapabilityResolver(CapabilityRegistry()).resolve_step(plan, step)

    assert result.status == "unresolved"
    assert result.reasons == ("no_capability_match",)
