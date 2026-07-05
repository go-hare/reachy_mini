"""Tests for the RobotRuntime 3D avatar adapter."""

from __future__ import annotations

import pytest

from reachy_mini.pipeline.frames import EmbodimentFrame
from reachy_mini.robot_runtime.adapters.avatar3d import Avatar3DAdapter
from reachy_mini.robot_runtime.contracts import LifecycleState, RobotCommand


def _command(command_type: str = "play_animation") -> RobotCommand:
    return RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="avatar3d",
        capability_id="avatar3d:animation:greet",
        command_type=command_type,
        payload={"turn_id": "turn_avatar", "semantic_tags": ["greet"]},
    )


@pytest.mark.asyncio
async def test_avatar3d_adapter_exposes_semantic_capabilities() -> None:
    """3D avatar capabilities are body-agnostic semantic inventory."""
    capabilities = await Avatar3DAdapter().capabilities()

    assert {capability.embodiment for capability in capabilities} == {"avatar3d"}
    assert any("greet" in capability.semantic_tags for capability in capabilities)
    assert any("attention_shift" in capability.semantic_tags for capability in capabilities)


@pytest.mark.asyncio
async def test_avatar3d_adapter_emits_embodiment_frame() -> None:
    """3D avatar commands become websocket-compatible embodiment frames."""
    frames: list[EmbodimentFrame] = []

    async def publish(frame: EmbodimentFrame) -> None:
        frames.append(frame)

    adapter = Avatar3DAdapter(publish_frame=publish)

    result = await adapter.execute(_command())
    state = await adapter.state()

    assert result.status.value == "completed"
    assert result.telemetry == {"frame_action": "avatar3d_animation"}
    assert frames == [
        EmbodimentFrame(
            action="avatar3d_animation",
            target="avatar3d",
            turn_id="turn_avatar",
            payload={
                "command_type": "play_animation",
                "capability_id": "avatar3d:animation:greet",
                "turn_id": "turn_avatar",
                "semantic_tags": ["greet"],
            },
            ts_ms=frames[0].ts_ms,
        )
    ]
    assert state.adapter_id == "avatar3d"


@pytest.mark.asyncio
async def test_avatar3d_adapter_maps_blendshape_action() -> None:
    """Blendshape commands get a distinct frame action."""
    frames: list[EmbodimentFrame] = []

    async def publish(frame: EmbodimentFrame) -> None:
        frames.append(frame)

    adapter = Avatar3DAdapter(publish_frame=publish)

    await adapter.execute(_command(command_type="set_blendshape"))

    assert frames[0].action == "avatar3d_blendshape"


@pytest.mark.asyncio
async def test_avatar3d_adapter_safe_idle_frame_is_explicit() -> None:
    """3D safe-idle output is explicit and not mapped to an animation command."""
    frames: list[EmbodimentFrame] = []

    async def publish(frame: EmbodimentFrame) -> None:
        frames.append(frame)

    adapter = Avatar3DAdapter(publish_frame=publish)

    result = await adapter.execute(_command(command_type="safe_idle"))

    assert result.telemetry == {
        "frame_action": "avatar3d_safe_idle",
        "safe_idle": True,
    }
    assert frames[0].action == "avatar3d_safe_idle"
    assert frames[0].payload["safe_idle"] is True


@pytest.mark.asyncio
async def test_avatar3d_adapter_config_state_and_cancel_are_structured() -> None:
    """3D avatar adapter exposes auditable state and structured cancellation."""
    adapter = Avatar3DAdapter()

    await adapter.configure({"renderer": "threejs", "safety_profile": "avatar"})
    state = await adapter.state()
    result = await adapter.cancel("command_1", "operator")

    assert adapter.context.safety_profile == "avatar"
    assert state.lifecycle is LifecycleState.INACTIVE
    assert state.metadata["options"] == {
        "renderer": "threejs",
        "safety_profile": "avatar",
    }
    assert result.status.value == "cancelled"
    assert result.error_message == "operator"
