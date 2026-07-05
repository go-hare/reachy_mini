"""Tests for the RobotRuntime MuJoCo adapter."""

from __future__ import annotations

import numpy as np
import pytest

from reachy_mini.robot_runtime.adapters.mujoco import MujocoAdapter
from reachy_mini.robot_runtime.contracts import LifecycleState, RobotCommand


class FakeBackend:
    """Daemon-like simulation backend with target fields."""

    def __init__(self) -> None:
        """Initialize target fields."""
        self.target_head_pose = None
        self.target_body_yaw = None
        self.target_antenna_joint_positions = None
        self.ik_required = False


@pytest.mark.asyncio
async def test_mujoco_adapter_executes_injected_sim_command() -> None:
    """MuJoCo adapter can delegate execution to a simulation callback."""
    seen: list[RobotCommand] = []

    async def execute(command: RobotCommand) -> dict[str, object]:
        seen.append(command)
        return {"scene": "empty"}

    adapter = MujocoAdapter(execute_command=execute)
    command = RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="mujoco",
        capability_id="mujoco:gaze:front",
        command_type="set_gaze",
    )

    result = await adapter.execute(command)

    assert seen == [command]
    assert result.status.value == "completed"
    assert result.telemetry == {"simulated": False, "scene": "empty"}


@pytest.mark.asyncio
async def test_mujoco_adapter_writes_daemon_like_backend_targets() -> None:
    """MuJoCo adapter can drive an existing daemon-like simulation backend."""
    backend = FakeBackend()
    adapter = MujocoAdapter(backend=backend)
    pose = np.eye(4, dtype=np.float64)
    pose[0, 3] = 0.02
    command = RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="mujoco",
        capability_id="mujoco:motion:greet",
        command_type="play_motion",
        payload={
            "target": {
                "head_pose": pose.tolist(),
                "body_yaw_deg": 15,
                "antennas_deg": [20, -20],
            }
        },
    )

    result = await adapter.execute(command)

    assert result.status.value == "completed"
    assert result.telemetry["simulated"] is False
    assert result.telemetry["backend"] == "FakeBackend"
    assert result.telemetry["updated"] == [
        "target_head_pose",
        "target_body_yaw",
        "target_antenna_joint_positions",
    ]
    assert backend.ik_required is True
    assert np.asarray(backend.target_head_pose).shape == (4, 4)
    assert backend.target_body_yaw == pytest.approx(np.radians(15))
    assert backend.target_antenna_joint_positions == pytest.approx(
        np.radians([20, -20])
    )


@pytest.mark.asyncio
async def test_mujoco_adapter_applies_default_head_pose_for_semantic_motion() -> None:
    """Generic semantic motion still produces a backend target."""
    backend = FakeBackend()
    adapter = MujocoAdapter(backend=backend)
    command = RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="mujoco",
        capability_id="mujoco:motion:greet",
        command_type="play_motion",
    )

    result = await adapter.execute(command)

    assert result.telemetry["updated"] == ["target_head_pose"]
    assert backend.ik_required is True
    assert np.asarray(backend.target_head_pose).shape == (4, 4)


@pytest.mark.asyncio
async def test_mujoco_adapter_safe_idle_does_not_move_backend() -> None:
    """Safety-degraded safe_idle commands are no-op simulation holds."""
    backend = FakeBackend()
    seen: list[RobotCommand] = []

    async def execute(command: RobotCommand) -> dict[str, object]:
        seen.append(command)
        return {"unexpected": True}

    adapter = MujocoAdapter(execute_command=execute, backend=backend)
    command = RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="mujoco",
        capability_id="mujoco:motion:greet",
        command_type="safe_idle",
    )

    result = await adapter.execute(command)

    assert result.status.value == "completed"
    assert result.telemetry == {
        "safe_idle": True,
        "source_capability": "mujoco:motion:greet",
    }
    assert seen == []
    assert backend.target_head_pose is None
    assert backend.ik_required is False


@pytest.mark.asyncio
async def test_mujoco_adapter_capabilities_are_reachy_shaped() -> None:
    """Simulation capabilities share semantic tags with hardware adapters."""
    capabilities = await MujocoAdapter().capabilities()

    assert {capability.embodiment for capability in capabilities} == {"mujoco"}
    assert any("greet" in capability.semantic_tags for capability in capabilities)


@pytest.mark.asyncio
async def test_mujoco_adapter_config_state_and_cancel_are_structured() -> None:
    """MuJoCo adapter exposes auditable state and structured cancellation."""
    adapter = MujocoAdapter()

    await adapter.configure({"safety_profile": "simulation", "scene": "smoke"})
    state = await adapter.state()
    result = await adapter.cancel("command_1", "operator")

    assert adapter.context.safety_profile == "simulation"
    assert state.lifecycle is LifecycleState.INACTIVE
    assert state.metadata["options"] == {
        "safety_profile": "simulation",
        "scene": "smoke",
    }
    assert result.status.value == "cancelled"
    assert result.error_message == "operator"
