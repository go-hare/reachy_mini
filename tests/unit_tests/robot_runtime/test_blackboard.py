"""Tests for RobotRuntime blackboard wrapper."""

from __future__ import annotations

from reachy_mini.robot_runtime import EmbodiedIntent, LifecycleState
from reachy_mini.robot_runtime.blackboard import RobotBlackboard
from reachy_mini.robot_runtime.state import RobotStateStore


def test_blackboard_initializes_required_keys() -> None:
    """Every documented blackboard key is present with a safe default."""

    board = RobotBlackboard.create(name="test-blackboard")
    snapshot = board.snapshot()

    assert snapshot["speech_state"] == "idle"
    assert snapshot["pending_intents"] == []
    assert snapshot["active_commands"] == []


def test_blackboard_orders_intents_by_priority() -> None:
    """The next intent is the highest priority pending item."""

    board = RobotBlackboard.create(name="test-intents")
    low = EmbodiedIntent(intent_type="idle", priority=10)
    high = EmbodiedIntent(intent_type="greet", priority=80)

    board.enqueue_intent(low)
    board.enqueue_intent(high)

    assert board.pop_next_intent() == high
    assert board.pop_next_intent() == low
    assert board.pop_next_intent() is None


def test_blackboard_tracks_robot_state_summary() -> None:
    """RobotState updates mirror key fields onto the blackboard."""

    store = RobotStateStore()
    store.set_lifecycle(LifecycleState.INACTIVE)
    store.set_active_turn("turn_1")
    state = store.snapshot().state
    board = RobotBlackboard.create(name="test-state")

    board.update_robot_state(state)

    assert board.snapshot()["robot_state"].lifecycle is LifecycleState.INACTIVE
    assert board.snapshot()["active_turn_id"] == "turn_1"
