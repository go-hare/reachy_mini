"""Tests for optional RobotRuntime bridge contracts."""

from __future__ import annotations

from reachy_mini.robot_runtime.bridges import (
    LeRobotBridge,
    PolicyServiceRequest,
    PolicyServiceResponse,
    ROS2Bridge,
)
from reachy_mini.robot_runtime.contracts import (
    AdapterResult,
    EmbodiedIntent,
    RobotCommand,
    RobotEvent,
    RobotState,
)


def test_ros2_bridge_serializes_contract_messages() -> None:
    """ROS bridge helpers do not require ROS imports."""
    bridge = ROS2Bridge(namespace="/demo")
    command = RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="mujoco",
        capability_id="cap",
        command_type="set_gaze",
    )

    message = bridge.command_message(command)

    assert message["topic"] == "/demo/robot_command"
    assert message["payload"]["command_type"] == "set_gaze"

    state_message = bridge.state_message(RobotState(active_turn_id="turn_1"))
    event_message = bridge.event_message(
        RobotEvent(source="runtime", event_type="intent_received", turn_id="turn_1")
    )

    assert state_message["topic"] == "/demo/robot_state"
    assert state_message["payload"]["active_turn_id"] == "turn_1"
    assert event_message["topic"] == "/demo/robot_event"
    assert event_message["payload"]["event_type"] == "intent_received"


def test_lerobot_bridge_builds_dataset_row() -> None:
    """LeRobot bridge produces offline dataset rows."""
    command = RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="mujoco",
        capability_id="cap",
        command_type="set_gaze",
    )
    result = AdapterResult(
        command_id=command.command_id,
        adapter_id="mujoco",
        status="completed",
    )

    row = LeRobotBridge().command_row(
        state=RobotState(),
        command=command,
        result=result,
    )

    assert row["dataset_version"] == "robot_runtime_v1"
    assert row["action"]["command_id"] == command.command_id
    assert row["result"]["status"] == "completed"


def test_policy_service_contracts_serialize_and_fallback() -> None:
    """Policy service contracts are JSON-friendly."""
    request = PolicyServiceRequest(
        state=RobotState(),
        intent=EmbodiedIntent(intent_type="greet"),
    )
    fallback = PolicyServiceResponse.safe_idle("no_policy")

    assert request.to_dict()["intent"]["intent_type"] == "greet"
    assert fallback.to_dict() == {
        "status": "safe_idle",
        "actions": [],
        "reason": "no_policy",
    }
    assert fallback.status == "safe_idle"
    assert fallback.reason == "no_policy"
