"""Tests for RobotRuntime safety supervisor."""

from __future__ import annotations

from dataclasses import replace

from reachy_mini.robot_runtime.contracts import (
    AdapterState,
    Capability,
    LifecycleState,
    RobotCommand,
    RobotState,
    RuntimeMode,
    SafetyState,
    SafetyStatus,
    now_ms,
)
from reachy_mini.robot_runtime.safety import SafetySupervisor


def _command(**kwargs) -> RobotCommand:
    values = {
        "plan_id": "plan",
        "step_id": "step",
        "adapter_id": "live2d",
        "capability_id": "cap",
        "command_type": "play_motion",
    }
    values.update(kwargs)
    return RobotCommand(
        **values,
    )


def test_safety_denies_when_runtime_not_active() -> None:
    """Commands cannot run before lifecycle is active."""
    supervisor = SafetySupervisor()
    decision = supervisor.evaluate(_command(), RobotState(lifecycle=LifecycleState.INACTIVE))

    assert decision.status is SafetyStatus.DENY
    assert decision.reasons == ["runtime_not_active:inactive"]


def test_safety_denies_emergency_stop() -> None:
    """Emergency stop always denies and requires operator action."""
    state = RobotState(
        lifecycle=LifecycleState.ACTIVE,
        safety_state=SafetyState(emergency_stop=True),
    )
    decision = SafetySupervisor().evaluate(_command(), state)

    assert decision.status is SafetyStatus.DENY
    assert decision.operator_action_required is True


def test_safety_degrades_long_duration() -> None:
    """Configured limits produce replacement commands."""
    state = RobotState(lifecycle=LifecycleState.ACTIVE)
    decision = SafetySupervisor(limits={"max_duration_ms": 500}).evaluate(
        _command(duration_ms=1000),
        state,
    )

    assert decision.status is SafetyStatus.DEGRADE
    assert decision.replacement_command is not None
    assert decision.replacement_command.duration_ms == 500
    assert decision.effective_limits == {"max_duration_ms": 500}


def test_safety_degrades_high_intensity() -> None:
    """Safety profiles can cap expressive intensity before adapter execution."""
    state = RobotState(lifecycle=LifecycleState.ACTIVE)
    decision = SafetySupervisor(limits={"max_intensity": 0.4}).evaluate(
        _command(payload={"intensity": 0.9}),
        state,
    )

    assert decision.status is SafetyStatus.DEGRADE
    assert decision.reasons == ["intensity_limited"]
    assert decision.effective_limits == {"max_intensity": 0.4}
    assert decision.replacement_command is not None
    assert decision.replacement_command.payload["intensity"] == 0.4


def test_safety_combines_multiple_command_limits() -> None:
    """Multiple configured limits should produce one composed replacement command."""
    state = RobotState(lifecycle=LifecycleState.ACTIVE)
    decision = SafetySupervisor(
        limits={
            "max_duration_ms": 500,
            "max_timeout_ms": 600,
            "max_intensity": 0.3,
        }
    ).evaluate(
        _command(
            duration_ms=1000,
            timeout_ms=1200,
            payload={"intensity": 0.9, "semantic_tags": ["greet"]},
        ),
        state,
    )

    assert decision.status is SafetyStatus.DEGRADE
    assert decision.reasons == [
        "duration_limited",
        "timeout_limited",
        "intensity_limited",
    ]
    assert decision.effective_limits == {
        "max_duration_ms": 500,
        "max_timeout_ms": 600,
        "max_intensity": 0.3,
    }
    assert decision.replacement_command is not None
    assert decision.replacement_command.duration_ms == 500
    assert decision.replacement_command.timeout_ms == 600
    assert decision.replacement_command.payload == {
        "intensity": 0.3,
        "semantic_tags": ["greet"],
    }


def test_speech_motion_conflict_degrades_to_safe_idle() -> None:
    """Large body motion is degraded while speech is active."""
    state = replace(
        RobotState(lifecycle=LifecycleState.ACTIVE, mode=RuntimeMode.HARDWARE),
        speech_state="speaking",
    )
    capability = Capability(
        capability_id="cap",
        adapter_id="reachy",
        embodiment="reachy",
        modality="motion",
        channels=["head"],
    )

    decision = SafetySupervisor().evaluate(_command(), state, capability)

    assert decision.status is SafetyStatus.DEGRADE
    assert decision.replacement_command is not None
    assert decision.replacement_command.command_type == "safe_idle"


def test_safety_denies_simulation_adapter_in_avatar_only_mode() -> None:
    """Runtime mode isolates avatar-only sessions from simulation commands."""
    state = RobotState(
        lifecycle=LifecycleState.ACTIVE,
        mode=RuntimeMode.AVATAR_ONLY,
        adapter_states={
            "mujoco": AdapterState(
                adapter_id="mujoco",
                lifecycle=LifecycleState.ACTIVE,
                mode=RuntimeMode.SIMULATION,
            )
        },
    )
    command = _command(adapter_id="mujoco")
    capability = Capability(
        capability_id="cap",
        adapter_id="mujoco",
        embodiment="mujoco",
        modality="motion",
        channels=["head"],
    )

    decision = SafetySupervisor().evaluate(command, state, capability)

    assert decision.status is SafetyStatus.DENY
    assert decision.reasons == [
        "mode_mismatch:runtime=avatar_only,adapter=simulation"
    ]


def test_safety_allows_avatar_output_in_simulation_mode() -> None:
    """Simulation sessions can still publish avatar presentation commands."""
    state = RobotState(
        lifecycle=LifecycleState.ACTIVE,
        mode=RuntimeMode.SIMULATION,
        adapter_states={
            "live2d": AdapterState(
                adapter_id="live2d",
                lifecycle=LifecycleState.ACTIVE,
                mode=RuntimeMode.AVATAR_ONLY,
            )
        },
    )

    decision = SafetySupervisor().evaluate(_command(), state)

    assert decision.status is SafetyStatus.ALLOW


def test_safety_denies_hardware_adapter_in_simulation_mode() -> None:
    """Simulation sessions cannot accidentally drive hardware adapters."""
    state = RobotState(
        lifecycle=LifecycleState.ACTIVE,
        mode=RuntimeMode.SIMULATION,
        adapter_states={
            "reachy": AdapterState(
                adapter_id="reachy",
                lifecycle=LifecycleState.ACTIVE,
                mode=RuntimeMode.HARDWARE,
            )
        },
    )
    command = _command(adapter_id="reachy")

    decision = SafetySupervisor().evaluate(command, state)

    assert decision.status is SafetyStatus.DENY
    assert decision.reasons == ["mode_mismatch:runtime=simulation,adapter=hardware"]


def test_safety_profile_can_restrict_runtime_modes() -> None:
    """Safety profiles can explicitly limit the allowed runtime mode."""
    state = RobotState(lifecycle=LifecycleState.ACTIVE, mode=RuntimeMode.HARDWARE)

    decision = SafetySupervisor(
        limits={"allowed_modes": ["simulation", "avatar_only"]}
    ).evaluate(_command(), state)

    assert decision.status is SafetyStatus.DENY
    assert decision.reasons == ["runtime_mode_blocked:hardware"]


def test_safety_profile_can_block_specific_adapters() -> None:
    """Safety profiles can disable a named adapter regardless of runtime mode."""
    state = RobotState(
        lifecycle=LifecycleState.ACTIVE,
        mode=RuntimeMode.HYBRID,
        adapter_states={
            "reachy": AdapterState(
                adapter_id="reachy",
                lifecycle=LifecycleState.ACTIVE,
                mode=RuntimeMode.HARDWARE,
            )
        },
    )

    decision = SafetySupervisor(limits={"blocked_adapters": ["reachy"]}).evaluate(
        _command(adapter_id="reachy"),
        state,
    )

    assert decision.status is SafetyStatus.DENY
    assert decision.reasons == ["adapter_blocked:reachy"]


def test_safety_allows_hardware_adapter_in_hybrid_mode() -> None:
    """Hybrid sessions can bridge approved hardware commands through safety."""
    state = RobotState(
        lifecycle=LifecycleState.ACTIVE,
        mode=RuntimeMode.HYBRID,
        adapter_states={
            "reachy": AdapterState(
                adapter_id="reachy",
                lifecycle=LifecycleState.ACTIVE,
                mode=RuntimeMode.HARDWARE,
            )
        },
    )
    command = _command(adapter_id="reachy")

    decision = SafetySupervisor().evaluate(command, state)

    assert decision.status is SafetyStatus.ALLOW


def test_safety_denies_hardware_when_watchdog_is_missing() -> None:
    """Hardware safety profiles can require a heartbeat watchdog."""
    state = RobotState(
        lifecycle=LifecycleState.ACTIVE,
        mode=RuntimeMode.HARDWARE,
        adapter_states={
            "reachy": AdapterState(
                adapter_id="reachy",
                lifecycle=LifecycleState.ACTIVE,
                mode=RuntimeMode.HARDWARE,
                metadata={},
            )
        },
    )

    decision = SafetySupervisor(limits={"watchdog_timeout_ms": 500}).evaluate(
        _command(adapter_id="reachy"),
        state,
    )

    assert decision.status is SafetyStatus.DENY
    assert decision.reasons == ["watchdog_missing:reachy"]
    assert decision.operator_action_required is True


def test_safety_denies_hardware_when_watchdog_is_stale() -> None:
    """Stale hardware heartbeat prevents command execution."""
    state = RobotState(
        lifecycle=LifecycleState.ACTIVE,
        mode=RuntimeMode.HARDWARE,
        adapter_states={
            "reachy": AdapterState(
                adapter_id="reachy",
                lifecycle=LifecycleState.ACTIVE,
                mode=RuntimeMode.HARDWARE,
                metadata={"last_heartbeat_ms": 1},
            )
        },
    )

    decision = SafetySupervisor(limits={"watchdog_timeout_ms": 1}).evaluate(
        _command(adapter_id="reachy"),
        state,
    )

    assert decision.status is SafetyStatus.DENY
    assert decision.reasons == ["watchdog_stale:reachy"]
    assert decision.operator_action_required is True


def test_safety_allows_hardware_when_watchdog_is_fresh() -> None:
    """Fresh hardware heartbeat satisfies the watchdog gate."""
    state = RobotState(
        lifecycle=LifecycleState.ACTIVE,
        mode=RuntimeMode.HARDWARE,
        adapter_states={
            "reachy": AdapterState(
                adapter_id="reachy",
                lifecycle=LifecycleState.ACTIVE,
                mode=RuntimeMode.HARDWARE,
                metadata={"last_heartbeat_ms": now_ms()},
            )
        },
    )

    decision = SafetySupervisor(limits={"watchdog_timeout_ms": 500}).evaluate(
        _command(adapter_id="reachy"),
        state,
    )

    assert decision.status is SafetyStatus.ALLOW
