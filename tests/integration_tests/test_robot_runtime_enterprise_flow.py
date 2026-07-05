"""Enterprise RobotRuntime integration smoke tests."""

from __future__ import annotations

import pytest

from reachy_mini.pipeline.frames import EmbodimentFrame
from reachy_mini.robot_runtime import (
    RobotIntentTools,
    RobotRuntime,
    RobotRuntimeConfig,
    RuntimeMode,
)
from reachy_mini.robot_runtime.adapters.live2d import Live2DAdapter
from reachy_mini.robot_runtime.adapters.mujoco import MujocoAdapter
from reachy_mini.robot_runtime.adapters.reachy import ReachyAdapter
from reachy_mini.runtime.live2d_avatar import Live2DCapabilities, Live2DNativeAction


class _FakeMini:
    """Minimal fake for proving safety denial does not hit hardware."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def goto_target(self, **kwargs: object) -> None:
        """Record calls that would have gone to hardware."""
        self.calls.append(dict(kwargs))


def _live2d_capabilities() -> Live2DCapabilities:
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
async def test_intent_tool_to_mujoco_adapter_flow_without_motion_names() -> None:
    """A generic intent tool call resolves through policy, scheduler, safety, and sim."""
    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.SIMULATION))
    await runtime.activate()
    await runtime.register_adapter(MujocoAdapter())
    tools = RobotIntentTools(runtime)

    result = await tools.emit_embodied_intent(intent_type="greet", turn_id="turn_sim")
    trace = await tools.query_robot_trace(intent_id=str(result["intent"]["intent_id"]))

    assert result["intent"]["intent_type"] == "greet"
    assert result["events"][-1]["event_type"] == "adapter_result"
    assert result["events"][-1]["status"] == "completed"
    assert result["events"][-1]["payload"]["adapter_id"] == "mujoco"
    assert trace["complete"] is True
    assert trace["adapter_ids"] == ["mujoco"]
    assert trace["events"][1]["payload"]["policy_name"] == "social_policy"


@pytest.mark.asyncio
async def test_live2d_adapter_flow_emits_embodiment_frame_from_semantic_intent() -> None:
    """Live2D files stay behind the adapter and surface as embodiment frames."""
    frames: list[EmbodimentFrame] = []

    async def publish_frame(frame: EmbodimentFrame) -> None:
        frames.append(frame)

    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.HARDWARE))
    await runtime.activate()
    await runtime.register_adapter(
        Live2DAdapter(_live2d_capabilities(), publish_frame=publish_frame)
    )
    tools = RobotIntentTools(runtime)

    result = await tools.emit_embodied_intent(intent_type="greet", turn_id="turn_ui")

    assert result["events"][-1]["status"] == "completed"
    assert frames == [
        EmbodimentFrame(
            action="live2d_motion",
            target="live2d",
            turn_id="turn_ui",
            payload={"name": "HuiShou"},
            ts_ms=frames[0].ts_ms,
        )
    ]


@pytest.mark.asyncio
async def test_reachy_hardware_safety_deny_does_not_call_mini() -> None:
    """Hardware adapter commands are blocked before execution when runtime is inactive."""
    mini = _FakeMini()
    runtime = RobotRuntime()
    await runtime.register_adapter(ReachyAdapter(mini=mini), config={"dry_run": False})
    tools = RobotIntentTools(runtime)

    result = await tools.emit_embodied_intent(intent_type="greet", turn_id="turn_hw")
    safety_event = next(
        event for event in result["events"] if event["event_type"] == "safety_decision"
    )

    assert safety_event["status"] == "deny"
    assert "runtime_not_active" in safety_event["reason"]
    assert not any(event["event_type"] == "adapter_result" for event in result["events"])
    assert mini.calls == []
