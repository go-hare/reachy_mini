"""Tests for the RobotRuntime Live2D adapter."""

from __future__ import annotations

import pytest

from reachy_mini.pipeline.frames import EmbodimentFrame
from reachy_mini.robot_runtime.adapters.live2d import Live2DAdapter
from reachy_mini.robot_runtime.capabilities import CapabilityRegistry
from reachy_mini.robot_runtime.contracts import (
    BehaviorPlan,
    BehaviorStep,
    LifecycleState,
    RobotCommand,
)
from reachy_mini.robot_runtime.resolver import CapabilityResolver
from reachy_mini.runtime.live2d_avatar import Live2DCapabilities, Live2DNativeAction


def _capabilities() -> Live2DCapabilities:
    return Live2DCapabilities(
        model_name="IceGirl",
        root_url="/static/assets/live2d/IceGirl",
        vtube_file="IceGirl.vtube.json",
        model_file="IceGirl.model3.json",
        idle_motion="DaiJi",
        motions=("DaiJi", "HuiShou"),
        expressions=("惊讶",),
        motion_details=(
            Live2DNativeAction(
                name="HuiShou",
                file_name="HuiShou.motion3.json",
                aliases=("greet", "wave"),
                duration_s=2.0,
            ),
        ),
        expression_details=(
            Live2DNativeAction(
                name="惊讶",
                file_name="惊讶.exp3.json",
                aliases=("surprise",),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_live2d_adapter_exposes_semantic_capabilities() -> None:
    """Live2D native files are converted into capability inventory."""
    adapter = Live2DAdapter(_capabilities())

    capabilities = await adapter.capabilities()

    assert [capability.modality for capability in capabilities] == [
        "motion",
        "expression",
    ]
    assert "greet" in capabilities[0].semantic_tags
    assert capabilities[0].source_asset == {"native_name": "HuiShou"}


@pytest.mark.asyncio
async def test_live2d_adapter_config_state_is_auditable() -> None:
    """Live2D adapter keeps profile options visible in adapter state."""
    adapter = Live2DAdapter(_capabilities())

    await adapter.configure({"safety_profile": "avatar", "source": "profile"})
    state = await adapter.state()

    assert adapter.context.safety_profile == "avatar"
    assert state.lifecycle is LifecycleState.INACTIVE
    assert state.metadata["options"] == {
        "safety_profile": "avatar",
        "source": "profile",
    }


@pytest.mark.asyncio
async def test_live2d_adapter_executes_resolved_command() -> None:
    """A semantic plan resolves and emits an EmbodimentFrame."""
    frames: list[EmbodimentFrame] = []

    async def publish(frame: EmbodimentFrame) -> None:
        frames.append(frame)

    adapter = Live2DAdapter(_capabilities(), publish_frame=publish)
    registry = CapabilityRegistry()
    registry.register_many(await adapter.capabilities())
    plan = BehaviorPlan(intent_id="intent", plan_id="plan", duration_ms=800)
    step = BehaviorStep(
        step_id="step",
        plan_id="plan",
        channel="gesture",
        capability_query={
            "semantic_tags": ["greet"],
            "modality": "motion",
            "payload": {"turn_id": "turn_1"},
        },
    )

    resolved = CapabilityResolver(registry).resolve_step(plan, step)
    assert resolved.command is not None
    result = await adapter.execute(resolved.command)

    assert result.status.value == "completed"
    assert frames == [
        EmbodimentFrame(
            action="live2d_motion",
            target="live2d",
            turn_id="turn_1",
            payload={"name": "HuiShou"},
            ts_ms=frames[0].ts_ms,
        )
    ]


@pytest.mark.asyncio
async def test_live2d_adapter_safe_idle_does_not_play_source_motion() -> None:
    """Safety-degraded safe_idle commands do not replay the original Live2D asset."""
    frames: list[EmbodimentFrame] = []

    async def publish(frame: EmbodimentFrame) -> None:
        frames.append(frame)

    adapter = Live2DAdapter(_capabilities(), publish_frame=publish)
    command = RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="live2d",
        capability_id="live2d:motion:HuiShou",
        command_type="safe_idle",
        payload={"turn_id": "turn_safe"},
    )

    result = await adapter.execute(command)

    assert result.status.value == "completed"
    assert result.telemetry == {
        "safe_idle": True,
        "source_capability": "live2d:motion:HuiShou",
    }
    assert frames == []


@pytest.mark.asyncio
async def test_live2d_adapter_cancel_returns_structured_result() -> None:
    """Cancellation is represented as AdapterResult."""
    result = await Live2DAdapter(_capabilities()).cancel("command_1", "operator")

    assert result.status.value == "cancelled"
    assert result.error_message == "operator"
