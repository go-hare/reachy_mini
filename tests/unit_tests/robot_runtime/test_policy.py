"""Tests for RobotRuntime planning policies."""

from __future__ import annotations

from reachy_mini.robot_runtime import (
    EmbodiedIntent,
    PolicyServiceRequest,
    PolicyServiceResponse,
    RobotPolicyEngine,
    TimingAnchor,
    create_default_policy_engine,
)
from reachy_mini.robot_runtime.policies import (
    AttentionPolicy,
    LearnedPolicy,
    SocialPolicy,
    SpeechSyncPolicy,
    TaskPolicy,
)


class _PolicyBackend:
    """Fake learned policy backend."""

    def __init__(self, response: PolicyServiceResponse) -> None:
        self.response = response
        self.requests: list[PolicyServiceRequest] = []

    def plan(self, request: PolicyServiceRequest) -> PolicyServiceResponse:
        """Return the configured fake response."""
        self.requests.append(request)
        return self.response


def test_default_policy_engine_routes_social_intent_to_gesture_plan() -> None:
    """Social intents compile to body-agnostic gesture plans."""
    engine = create_default_policy_engine()
    intent = EmbodiedIntent(
        intent_type="greet",
        turn_id="turn_policy",
        constraints={
            "adapter_id": "mujoco",
            "duration_ms": 900,
            "timeout_ms": 450,
        },
    )

    result = engine.plan_intent(intent)
    plan = result.plan
    step = plan.steps[0]

    assert result.policy_name == "social_policy"
    assert plan.channels == ["gesture"]
    assert plan.duration_ms == 900
    assert plan.constraints["policy_name"] == "social_policy"
    assert plan.constraints["behavior_node"] == "RobotPolicyEngine/social_policy"
    assert step.plan_id == plan.plan_id
    assert step.timeout_ms == 450
    assert step.capability_query["semantic_tags"] == ["greet"]
    assert step.capability_query["adapter_id"] == "mujoco"
    assert step.capability_query["payload"]["policy_name"] == "social_policy"
    assert (
        step.capability_query["payload"]["behavior_node"]
        == "RobotPolicyEngine/social_policy"
    )


def test_policy_engine_respects_explicit_channels_over_defaults() -> None:
    """Caller constraints can override policy default channels."""
    engine = RobotPolicyEngine(policies=(SocialPolicy(),))
    intent = EmbodiedIntent(
        intent_type="greet",
        constraints={"channels": ["face", "gesture"]},
    )

    plan = engine.plan_intent(intent).plan

    assert plan.channels == ["face", "gesture"]
    assert plan.steps[0].channel == "face"


def test_speech_sync_policy_assigns_speech_timing_anchor() -> None:
    """Speech lifecycle intents get speech-aware timing anchors."""
    engine = RobotPolicyEngine(policies=(SpeechSyncPolicy(),))
    intent = EmbodiedIntent(intent_type="think")

    result = engine.plan_intent(intent)

    assert result.policy_name == "speech_sync_policy"
    assert result.plan.timing_anchor is TimingAnchor.SPEECH_PREPARE
    assert result.plan.channels == ["face"]
    assert result.plan.steps[0].capability_query["semantic_tags"] == ["think"]


def test_task_policy_assigns_task_start_and_command_type() -> None:
    """Task intents compile to task-start plans without bypassing resolver."""
    engine = RobotPolicyEngine(policies=(TaskPolicy(),))
    intent = EmbodiedIntent(intent_type="task_execute", target={"task_type": "inspect"})

    result = engine.plan_intent(intent)
    step = result.plan.steps[0]

    assert result.policy_name == "task_policy"
    assert result.plan.timing_anchor is TimingAnchor.TASK_START
    assert result.plan.channels == ["navigation"]
    assert step.capability_query["command_type"] == "execute_task"
    assert step.capability_query["payload"]["target"] == {"task_type": "inspect"}


def test_attention_policy_maps_to_gaze_command() -> None:
    """Attention shifts are planned as gaze/head commands."""
    engine = RobotPolicyEngine(policies=(AttentionPolicy(),))
    intent = EmbodiedIntent(intent_type="attention_shift", target={"x": 0.1, "y": 0.2})

    result = engine.plan_intent(intent)
    step = result.plan.steps[0]

    assert result.policy_name == "attention_policy"
    assert result.plan.channels == ["gaze", "head"]
    assert step.capability_query["command_type"] == "set_gaze"
    assert step.capability_query["semantic_tags"] == ["attention_shift", "look"]


def test_learned_policy_converts_backend_actions_to_behavior_plan() -> None:
    """Learned policy output remains a plan, not an executable command."""
    backend = _PolicyBackend(
        PolicyServiceResponse(
            status="planned",
            reason="openpi_fixture",
            actions=[
                {
                    "channel": "gesture",
                    "semantic_tags": ["greet"],
                    "timeout_ms": 650,
                    "payload": {"policy_action_id": "a1"},
                }
            ],
        )
    )
    engine = RobotPolicyEngine(policies=(LearnedPolicy(backend), SocialPolicy()))
    intent = EmbodiedIntent(
        intent_type="greet",
        constraints={"policy_backend": "openpi"},
        turn_id="turn_policy",
    )

    result = engine.plan_intent(intent)
    plan = result.plan
    step = plan.steps[0]

    assert result.policy_name == "learned_policy"
    assert result.reasons == ("openpi_fixture",)
    assert backend.requests[0].intent.intent_id == intent.intent_id
    assert plan.channels == ["gesture"]
    assert plan.constraints["policy_service_status"] == "planned"
    assert plan.constraints["behavior_node"] == "RobotPolicyEngine/learned_policy"
    assert step.capability_query["adapter_id"] is None
    assert "command_type" not in step.capability_query
    assert step.capability_query["payload"]["policy_action_id"] == "a1"
    assert (
        step.capability_query["payload"]["behavior_node"]
        == "RobotPolicyEngine/learned_policy"
    )
    assert "command_id" not in step.capability_query


def test_default_policy_engine_inserts_learned_policy_when_backend_is_present() -> None:
    """Configured learned policy backend takes priority when explicitly requested."""
    backend = _PolicyBackend(
        PolicyServiceResponse(
            status="planned",
            actions=[{"channel": "gesture", "semantic_tags": ["greet"]}],
        )
    )
    engine = create_default_policy_engine(policy_backend=backend)
    intent = EmbodiedIntent(
        intent_type="greet",
        constraints={"use_learned_policy": True},
    )

    result = engine.plan_intent(intent)

    assert result.policy_name == "learned_policy"
    assert backend.requests


def test_learned_policy_rejects_direct_command_fields() -> None:
    """Learned policies cannot bypass resolver or safety with raw command fields."""
    backend = _PolicyBackend(
        PolicyServiceResponse(
            status="planned",
            actions=[
                {
                    "channel": "gesture",
                    "semantic_tags": ["greet"],
                    "command_id": "forbidden",
                }
            ],
        )
    )
    engine = RobotPolicyEngine(policies=(LearnedPolicy(backend),))
    intent = EmbodiedIntent(
        intent_type="greet",
        constraints={"use_learned_policy": True},
    )

    try:
        engine.plan_intent(intent)
    except ValueError as exc:
        assert "must stay body-agnostic" in str(exc)
        assert "command_id" in str(exc)
    else:
        raise AssertionError("expected learned policy direct command rejection")


def test_learned_policy_rejects_adapter_private_action_fields() -> None:
    """External policy actions cannot choose adapter-private command routing."""
    backend = _PolicyBackend(
        PolicyServiceResponse(
            status="planned",
            actions=[
                {
                    "channel": "gesture",
                    "semantic_tags": ["greet"],
                    "adapter_id": "mujoco",
                    "command_type": "play_motion",
                }
            ],
        )
    )
    engine = RobotPolicyEngine(policies=(LearnedPolicy(backend),))
    intent = EmbodiedIntent(
        intent_type="greet",
        constraints={"use_learned_policy": True},
    )

    try:
        engine.plan_intent(intent)
    except ValueError as exc:
        assert "must stay body-agnostic" in str(exc)
        assert "adapter_id" in str(exc)
    else:
        raise AssertionError("expected learned policy adapter-private rejection")


def test_learned_policy_rejects_nested_adapter_private_payload() -> None:
    """Nested payloads from external policies are also body-agnostic."""
    backend = _PolicyBackend(
        PolicyServiceResponse(
            status="planned",
            actions=[
                {
                    "channel": "gesture",
                    "semantic_tags": ["greet"],
                    "payload": {"motor_targets": {"body_rotation": 10}},
                }
            ],
        )
    )
    engine = RobotPolicyEngine(policies=(LearnedPolicy(backend),))
    intent = EmbodiedIntent(
        intent_type="greet",
        constraints={"use_learned_policy": True},
    )

    try:
        engine.plan_intent(intent)
    except ValueError as exc:
        assert "must stay body-agnostic" in str(exc)
        assert "payload.motor_targets" in str(exc)
    else:
        raise AssertionError("expected learned policy nested payload rejection")
