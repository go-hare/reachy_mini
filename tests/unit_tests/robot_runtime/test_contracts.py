"""Tests for RobotRuntime contracts and validation."""

from __future__ import annotations

import pytest

from reachy_mini.robot_runtime import (
    AdapterResult,
    AdapterResultStatus,
    BehaviorPlan,
    BehaviorStep,
    Capability,
    EmbodiedIntent,
    IntentType,
    RobotCommand,
    SafetyDecision,
    SafetyStatus,
    SpeechRelation,
    TimingAnchor,
)


def test_embodied_intent_coerces_enums_and_serializes() -> None:
    """Intent contracts accept strings but serialize canonical values."""

    intent = EmbodiedIntent(
        intent_type="greet",
        speech_relation="before_speech",
        timing_anchor="speech_start",
        modalities=["face", "gesture"],
        priority=60,
        intensity=0.7,
    )

    assert intent.intent_type is IntentType.GREET
    assert intent.speech_relation is SpeechRelation.BEFORE_SPEECH
    assert intent.timing_anchor is TimingAnchor.SPEECH_START
    assert intent.to_dict()["intent_type"] == "greet"
    assert intent.to_dict()["speech_relation"] == "before_speech"


def test_embodied_intent_rejects_invalid_bounds() -> None:
    """Intensity and priority are validated at construction time."""

    with pytest.raises(ValueError, match="intensity"):
        EmbodiedIntent(intent_type="greet", intensity=1.2)

    with pytest.raises(ValueError, match="priority"):
        EmbodiedIntent(intent_type="greet", priority=101)


def test_channel_fields_are_controlled_enums() -> None:
    """Contracts reject unknown modalities or channels."""

    with pytest.raises(ValueError, match="unsupported channels"):
        EmbodiedIntent(intent_type="greet", modalities=["live2d_motion"])

    with pytest.raises(ValueError, match="unsupported channels"):
        BehaviorStep(channel="raw_motor", capability_query={})

    with pytest.raises(ValueError, match="unsupported channels"):
        Capability(
            capability_id="cap",
            adapter_id="adapter",
            embodiment="live2d",
            modality="motion",
            channels=["raw_motor"],
        )


def test_behavior_plan_coerces_timing_and_fallback() -> None:
    """BehaviorPlan normalizes enum-like fields."""

    step = BehaviorStep(channel="gesture", capability_query={"semantic_tags": ["greet"]})
    plan = BehaviorPlan(
        intent_id="intent_1",
        timing_anchor="speech_start",
        fallback_policy="degrade",
        channels=["gesture"],
        steps=[step],
    )

    assert plan.timing_anchor is TimingAnchor.SPEECH_START
    assert plan.fallback_policy.value == "degrade"
    assert plan.to_dict()["steps"][0]["channel"] == "gesture"


def test_command_and_result_validate_time_order() -> None:
    """RobotCommand and AdapterResult reject invalid time fields."""

    with pytest.raises(ValueError, match="timeout_ms"):
        RobotCommand(
            plan_id="plan",
            step_id="step",
            adapter_id="adapter",
            capability_id="cap",
            command_type="play_motion",
            timeout_ms=-1,
        )

    with pytest.raises(ValueError, match="ended_at_ms"):
        AdapterResult(
            command_id="cmd",
            adapter_id="adapter",
            status=AdapterResultStatus.COMPLETED,
            started_at_ms=20,
            ended_at_ms=10,
        )


def test_safety_decision_serializes_status_and_replacement() -> None:
    """Safety decisions keep structured reasons and nested replacement commands."""

    replacement = RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="adapter",
        capability_id="safe_idle",
        command_type="safe_idle",
    )
    decision = SafetyDecision(
        command_id="cmd",
        status=SafetyStatus.DEGRADE,
        reasons=["speech_active"],
        replacement_command=replacement,
    )

    data = decision.to_dict()

    assert data["status"] == "degrade"
    assert data["reasons"] == ["speech_active"]
    assert data["replacement_command"]["command_type"] == "safe_idle"
