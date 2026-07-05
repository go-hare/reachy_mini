"""Tests for the RobotRuntime py_trees behavior tree."""

from __future__ import annotations

import py_trees

from reachy_mini.robot_runtime import EmbodiedIntent, LifecycleState, SafetyState
from reachy_mini.robot_runtime.behavior_tree import build_robot_tree
from reachy_mini.robot_runtime.state import RobotStateStore


def test_behavior_tree_blocks_until_lifecycle_is_ready() -> None:
    """The lifecycle gate prevents unconfigured execution."""
    tree = build_robot_tree()

    assert tree.tick() is py_trees.common.Status.FAILURE


def test_behavior_tree_consumes_intent_when_inactive() -> None:
    """Inactive is configured enough for the tree to process pending intent state."""
    state_store = RobotStateStore()
    state_store.set_lifecycle(LifecycleState.INACTIVE)
    tree = build_robot_tree()
    tree.blackboard.update_robot_state(state_store.snapshot().state)
    intent = EmbodiedIntent(intent_type="greet", priority=60)
    tree.blackboard.enqueue_intent(intent)

    assert tree.tick() is py_trees.common.Status.SUCCESS

    active_plan = tree.blackboard.snapshot()["active_plan"]
    assert active_plan["intent_id"] == intent.intent_id
    assert active_plan["intent_type"] == "greet"

    snapshot = tree.snapshot()
    assert snapshot["root_status"] == "SUCCESS"
    assert snapshot["status_counts"] == {"SUCCESS": 4}
    assert snapshot["success_nodes"] == [
        "RobotRuntimeRoot",
        "RobotRuntimeRoot/SafetyGate",
        "RobotRuntimeRoot/LifecycleGate",
        "RobotRuntimeRoot/TurnCoordinator",
    ]
    assert snapshot["failure_nodes"] == []
    assert snapshot["blackboard"]["active_plan"]["intent_id"] == intent.intent_id
    assert snapshot["blackboard"]["robot_state"]["lifecycle"] == "inactive"


def test_behavior_tree_safety_gate_blocks_emergency_stop() -> None:
    """Emergency stop takes precedence over lifecycle readiness."""
    state_store = RobotStateStore()
    state_store.set_lifecycle(LifecycleState.INACTIVE)
    state_store.set_safety_state(SafetyState(emergency_stop=True))
    tree = build_robot_tree()
    tree.blackboard.update_robot_state(state_store.snapshot().state)

    assert tree.tick() is py_trees.common.Status.FAILURE

    snapshot = tree.snapshot()
    assert snapshot["root_status"] == "FAILURE"
    assert "RobotRuntimeRoot/SafetyGate" in snapshot["failure_nodes"]
    assert "RobotRuntimeRoot/LifecycleGate" in snapshot["invalid_nodes"]
