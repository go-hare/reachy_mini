"""Tests for the RobotRuntime Reachy hardware adapter."""

from __future__ import annotations

import math

import numpy as np
import pytest

from reachy_mini.robot_runtime.adapters.reachy import ReachyAdapter
from reachy_mini.robot_runtime.contracts import RobotCommand
from reachy_mini.utils.interpolation import InterpolationTechnique


class FakeMini:
    """Fake hardware SDK object."""

    def __init__(self) -> None:
        """Initialize fake call recording."""
        self.calls: list[tuple[str, dict[str, object]]] = []

    def goto_target(self, **kwargs: object) -> None:
        """Record goto_target calls."""
        self.calls.append(("goto_target", kwargs))

    def set_target(self, **kwargs: object) -> None:
        """Record set_target calls."""
        self.calls.append(("set_target", kwargs))

    def look_at_world(
        self,
        x: float,
        y: float,
        z: float,
        duration: float,
        perform_movement: bool,
    ) -> None:
        """Record look_at_world calls."""
        self.calls.append(
            (
                "look_at_world",
                {
                    "x": x,
                    "y": y,
                    "z": z,
                    "duration": duration,
                    "perform_movement": perform_movement,
                },
            )
        )

    def play_move(self, **kwargs: object) -> None:
        """Record play_move calls."""
        self.calls.append(("play_move", kwargs))


def _command() -> RobotCommand:
    return RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="reachy",
        capability_id="reachy:gaze:front",
        command_type="set_gaze",
        duration_ms=500,
    )


@pytest.mark.asyncio
async def test_reachy_adapter_defaults_to_dry_run() -> None:
    """Reachy adapter never touches hardware unless dry_run is explicitly false."""
    mini = FakeMini()
    adapter = ReachyAdapter(mini=mini)

    result = await adapter.execute(_command())
    state = await adapter.state()

    assert result.status.value == "completed"
    assert result.telemetry == {"dry_run": True, "command_type": "set_gaze"}
    assert mini.calls == []
    assert state.metadata["dry_run"] is True
    assert isinstance(state.metadata["last_heartbeat_ms"], int)


@pytest.mark.asyncio
async def test_reachy_adapter_can_execute_when_dry_run_disabled() -> None:
    """Hardware execution requires an explicit dry_run=false config."""
    mini = FakeMini()
    adapter = ReachyAdapter(mini=mini)
    await adapter.configure({"dry_run": False})

    result = await adapter.execute(_command())

    assert result.telemetry == {"dry_run": False, "command_type": "set_gaze"}
    assert mini.calls[0][0] == "goto_target"
    assert mini.calls[0][1]["duration"] == 0.5
    assert np.asarray(mini.calls[0][1]["head"]).shape == (4, 4)


@pytest.mark.asyncio
async def test_reachy_adapter_safe_idle_never_calls_hardware() -> None:
    """Safety-degraded safe_idle commands never move the physical robot."""
    mini = FakeMini()
    adapter = ReachyAdapter(mini=mini)
    await adapter.configure({"dry_run": False})
    command = RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="reachy",
        capability_id="reachy:motion:greet",
        command_type="safe_idle",
    )

    result = await adapter.execute(command)

    assert result.status.value == "completed"
    assert result.telemetry == {
        "dry_run": False,
        "safe_idle": True,
        "source_capability": "reachy:motion:greet",
        "command_type": "safe_idle",
    }
    assert mini.calls == []


@pytest.mark.asyncio
async def test_reachy_adapter_executes_look_at_world_target() -> None:
    """Gaze commands with target points use the SDK look_at_world helper."""
    mini = FakeMini()
    adapter = ReachyAdapter(mini=mini)
    await adapter.configure({"dry_run": False})
    command = RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="reachy",
        capability_id="reachy:gaze:front",
        command_type="set_gaze",
        duration_ms=800,
        payload={"target": {"point": [1.0, 0.2, 0.4]}},
    )

    result = await adapter.execute(command)

    assert result.telemetry == {"dry_run": False, "command_type": "set_gaze"}
    assert mini.calls == [
        (
            "look_at_world",
            {
                "x": 1.0,
                "y": 0.2,
                "z": 0.4,
                "duration": 0.8,
                "perform_movement": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_reachy_adapter_executes_explicit_pose_antennas_and_body_yaw() -> None:
    """Motion commands map explicit semantic payload fields to goto_target."""
    mini = FakeMini()
    adapter = ReachyAdapter(mini=mini)
    await adapter.configure({"dry_run": False})
    command = RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="reachy",
        capability_id="reachy:motion:greet",
        command_type="play_motion",
        duration_ms=1200,
        payload={
            "target": {
                "pitch_deg": -5,
                "yaw_deg": 10,
                "body_yaw_deg": 15,
                "antennas_deg": [20, -20],
            },
            "method": "linear",
        },
    )

    await adapter.execute(command)

    method, kwargs = mini.calls[0]
    assert method == "goto_target"
    assert kwargs["duration"] == 1.2
    assert kwargs["method"] is InterpolationTechnique.LINEAR
    assert kwargs["body_yaw"] == pytest.approx(math.radians(15))
    assert kwargs["antennas"] == pytest.approx([math.radians(20), math.radians(-20)])
    assert np.asarray(kwargs["head"]).shape == (4, 4)


@pytest.mark.asyncio
async def test_reachy_adapter_set_target_strips_interpolation_method() -> None:
    """Immediate set_target commands omit goto-only interpolation options."""
    mini = FakeMini()
    adapter = ReachyAdapter(mini=mini)
    await adapter.configure({"dry_run": False})
    command = RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="reachy",
        capability_id="reachy:gaze:front",
        command_type="set_target",
        payload={
            "yaw_rad": 0.1,
            "antennas": [0.2, -0.2],
            "method": "linear",
        },
    )

    await adapter.execute(command)

    method, kwargs = mini.calls[0]
    assert method == "set_target"
    assert "method" not in kwargs
    assert kwargs["antennas"] == [0.2, -0.2]
    assert np.asarray(kwargs["head"]).shape == (4, 4)


@pytest.mark.asyncio
async def test_reachy_adapter_play_move_for_recorded_moves() -> None:
    """Recorded move commands call play_move with speed and initial duration."""
    mini = FakeMini()
    adapter = ReachyAdapter(mini=mini)
    await adapter.configure({"dry_run": False})
    move = object()
    command = RobotCommand(
        plan_id="plan",
        step_id="step",
        adapter_id="reachy",
        capability_id="reachy:motion:greet",
        command_type="play_move",
        duration_ms=1000,
        payload={"move": move, "speed": 1.25},
    )

    await adapter.execute(command)

    assert mini.calls == [
        (
            "play_move",
            {
                "move": move,
                "initial_goto_duration": 1.0,
                "speed": 1.25,
            },
        )
    ]


@pytest.mark.asyncio
async def test_reachy_adapter_cancel_returns_structured_result() -> None:
    """Hardware adapter cancellation is represented as AdapterResult."""
    result = await ReachyAdapter().cancel("command_1", "operator")

    assert result.status.value == "cancelled"
    assert result.error_message == "operator"
