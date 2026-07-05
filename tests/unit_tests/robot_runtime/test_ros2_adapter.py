"""Tests for the RobotRuntime ROS2 adapter facade."""

from __future__ import annotations

import pytest

from reachy_mini.robot_runtime import (
    EmbodiedIntent,
    RobotRuntime,
    RobotRuntimeConfig,
    RuntimeMode,
)
from reachy_mini.robot_runtime.adapters.ros2 import ROS2Adapter
from reachy_mini.robot_runtime.contracts import RobotCommand, RobotEvent, RobotState


def _command() -> RobotCommand:
    return RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="ros2",
        capability_id="ros2:motion:greet",
        command_type="play_motion",
        payload={"turn_id": "turn_ros"},
    )


@pytest.mark.asyncio
async def test_ros2_adapter_exposes_bridge_capabilities() -> None:
    """ROS2 adapter exposes semantic capabilities without importing ROS."""
    adapter = ROS2Adapter()

    capabilities = await adapter.capabilities()

    assert {capability.embodiment for capability in capabilities} == {"ros2"}
    assert any("greet" in capability.semantic_tags for capability in capabilities)
    assert any("task_execute" in capability.semantic_tags for capability in capabilities)


@pytest.mark.asyncio
async def test_ros2_adapter_publishes_bridge_message() -> None:
    """Approved commands are serialized into ROS-friendly messages."""
    messages = []

    async def publish(message):
        messages.append(message)

    adapter = ROS2Adapter(publish_message=publish)
    await adapter.configure({"namespace": "/reachy_test"})

    result = await adapter.execute(_command())
    state = await adapter.state()

    assert result.status.value == "completed"
    assert messages[0]["topic"] == "/reachy_test/robot_command"
    assert messages[0]["payload"]["command_type"] == "play_motion"
    assert result.telemetry["message"] == messages[0]
    assert result.telemetry["safe_idle"] is False
    assert state.metadata["namespace"] == "/reachy_test"


@pytest.mark.asyncio
async def test_ros2_adapter_publishes_safe_idle_as_explicit_command() -> None:
    """ROS2 safe-idle bridge messages are explicit stop/idle commands."""
    messages = []

    async def publish(message):
        messages.append(message)

    adapter = ROS2Adapter(publish_message=publish)
    command = RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="ros2",
        capability_id="ros2:motion:greet",
        command_type="safe_idle",
    )

    result = await adapter.execute(command)

    assert result.status.value == "completed"
    assert result.telemetry["safe_idle"] is True
    assert messages[0]["payload"]["command_type"] == "safe_idle"


@pytest.mark.asyncio
async def test_ros2_adapter_publishes_state_and_event_messages() -> None:
    """Runtime state and events can be serialized as ROS-friendly messages."""
    messages = []

    async def publish(message):
        messages.append(message)

    adapter = ROS2Adapter(publish_message=publish)
    await adapter.configure({"namespace": "/reachy_test"})

    state_message = await adapter.publish_runtime_state(
        RobotState(active_turn_id="turn_ros")
    )
    event_message = await adapter.publish_runtime_event(
        RobotEvent(
            source="runtime",
            event_type="intent_received",
            turn_id="turn_ros",
        )
    )
    state = await adapter.state()

    assert state_message["topic"] == "/reachy_test/robot_state"
    assert event_message["topic"] == "/reachy_test/robot_event"
    assert messages == [state_message, event_message]
    assert state.lifecycle.value == "inactive"


@pytest.mark.asyncio
async def test_runtime_mirrors_state_and_events_to_ros2_adapter() -> None:
    """Registered ROS2 adapters receive Runtime state and event mirror messages."""
    messages = []

    async def publish(message):
        messages.append(message)

    runtime = RobotRuntime(config=RobotRuntimeConfig(mode=RuntimeMode.HYBRID))
    adapter = ROS2Adapter(publish_message=publish)

    await runtime.register_adapter(adapter, config={"namespace": "/reachy_runtime"})
    await runtime.accept_intent(
        EmbodiedIntent(intent_type="greet", turn_id="turn_ros")
    )

    topics = [message["topic"] for message in messages]
    event_types = [
        message["payload"]["event_type"]
        for message in messages
        if message["topic"] == "/reachy_runtime/robot_event"
    ]

    assert "/reachy_runtime/robot_state" in topics
    assert "/reachy_runtime/robot_event" in topics
    assert "adapter_registered" in event_types
    assert "intent_received" in event_types


@pytest.mark.asyncio
async def test_ros2_adapter_cancel_returns_structured_result() -> None:
    """Cancellation is represented as AdapterResult."""
    result = await ROS2Adapter().cancel("command_1", "operator")

    assert result.status.value == "cancelled"
    assert result.error_message == "operator"
