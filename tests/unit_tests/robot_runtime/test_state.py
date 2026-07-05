"""Tests for RobotRuntime state store."""

from __future__ import annotations

from reachy_mini.robot_runtime import AdapterState, LifecycleState, RuntimeMode, SafetyState
from reachy_mini.robot_runtime.state import RobotStateStore


def test_state_store_updates_revisioned_snapshots() -> None:
    """State updates are immutable snapshots with monotonically increasing revisions."""

    store = RobotStateStore()
    first = store.snapshot()
    second = store.set_lifecycle(LifecycleState.INACTIVE)
    third = store.set_adapter_state(
        AdapterState(
            adapter_id="live2d",
            lifecycle=LifecycleState.ACTIVE,
            mode=RuntimeMode.AVATAR_ONLY,
        )
    )
    fourth = store.set_safety_state(SafetyState(emergency_stop=True, reasons=["operator"]))

    assert first.revision == 0
    assert second.revision == 1
    assert third.revision == 2
    assert fourth.revision == 3
    assert first.state.lifecycle is LifecycleState.UNCONFIGURED
    assert second.state.lifecycle is LifecycleState.INACTIVE
    assert third.state.adapter_states["live2d"].lifecycle is LifecycleState.ACTIVE
    assert fourth.state.safety_state.emergency_stop is True
