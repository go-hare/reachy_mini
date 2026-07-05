"""py_trees behavior tree for RobotRuntime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import py_trees

from reachy_mini.robot_runtime.blackboard import RobotBlackboard
from reachy_mini.robot_runtime.contracts import LifecycleState


class _LifecycleGate(py_trees.behaviour.Behaviour):
    """Allow tree progress only when the runtime is active or inactive."""

    def __init__(self, blackboard: RobotBlackboard) -> None:
        super().__init__("LifecycleGate")
        self._blackboard = blackboard

    def update(self) -> py_trees.common.Status:
        """Return failure for error/shutdown/unconfigured states."""
        state = self._blackboard.client.get("robot_state")
        if state.lifecycle in {LifecycleState.ACTIVE, LifecycleState.INACTIVE}:
            return py_trees.common.Status.SUCCESS
        return py_trees.common.Status.FAILURE


class _SafetyGate(py_trees.behaviour.Behaviour):
    """Block execution when emergency stop is active."""

    def __init__(self, blackboard: RobotBlackboard) -> None:
        super().__init__("SafetyGate")
        self._blackboard = blackboard

    def update(self) -> py_trees.common.Status:
        """Return failure when safety says emergency stop."""
        state = self._blackboard.client.get("robot_state")
        if state.safety_state.emergency_stop:
            return py_trees.common.Status.FAILURE
        return py_trees.common.Status.SUCCESS


class _TurnCoordinator(py_trees.behaviour.Behaviour):
    """Consume pending intents without performing blocking adapter I/O."""

    def __init__(self, blackboard: RobotBlackboard) -> None:
        super().__init__("TurnCoordinator")
        self._blackboard = blackboard

    def update(self) -> py_trees.common.Status:
        """Promote one pending intent into the current active plan record."""
        intent = self._blackboard.pop_next_intent()
        if intent is None:
            return py_trees.common.Status.SUCCESS
        self._blackboard.client.set(
            "active_plan",
            {
                "intent_id": intent.intent_id,
                "intent_type": intent.intent_type.value,
                "status": "draft",
            },
        )
        return py_trees.common.Status.SUCCESS


@dataclass(slots=True)
class RobotBehaviorTree:
    """Small wrapper around the py_trees tree."""

    blackboard: RobotBlackboard
    tree: py_trees.trees.BehaviourTree

    def tick(self) -> py_trees.common.Status:
        """Tick once and return the root status."""
        self.tree.tick()
        return self.tree.root.status

    def snapshot(self) -> dict[str, object]:
        """Return debug status for all behavior tree nodes and blackboard."""
        return RobotBehaviorTreeSnapshot.from_root(
            self.tree.root,
            blackboard=_jsonable(self.blackboard.snapshot()),
        ).to_dict()


@dataclass(frozen=True, slots=True)
class RobotBehaviorNodeSnapshot:
    """Serializable status for one behavior tree node."""

    name: str
    status: str
    path: str
    depth: int
    children: tuple["RobotBehaviorNodeSnapshot", ...] = ()

    @classmethod
    def from_node(
        cls,
        node: py_trees.behaviour.Behaviour,
        *,
        path: str = "",
        depth: int = 0,
    ) -> "RobotBehaviorNodeSnapshot":
        """Create a recursive node snapshot from a py_trees behavior."""
        current_path = f"{path}/{node.name}" if path else node.name
        children = tuple(
            cls.from_node(child, path=current_path, depth=depth + 1)
            for child in getattr(node, "children", ())
        )
        return cls(
            name=node.name,
            status=node.status.value,
            path=current_path,
            depth=depth,
            children=children,
        )

    def flatten(self) -> tuple["RobotBehaviorNodeSnapshot", ...]:
        """Return this node and descendants in pre-order."""
        descendants = tuple(child for item in self.children for child in item.flatten())
        return (self, *descendants)

    def to_dict(self) -> dict[str, object]:
        """Serialize this node snapshot."""
        return {
            "name": self.name,
            "status": self.status,
            "path": self.path,
            "depth": self.depth,
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(frozen=True, slots=True)
class RobotBehaviorTreeSnapshot:
    """Serializable behavior tree snapshot with status indexes."""

    root: RobotBehaviorNodeSnapshot
    blackboard: dict[str, object]

    @classmethod
    def from_root(
        cls,
        root: py_trees.behaviour.Behaviour,
        *,
        blackboard: dict[str, object],
    ) -> "RobotBehaviorTreeSnapshot":
        """Create a full tree snapshot from the root node."""
        return cls(
            root=RobotBehaviorNodeSnapshot.from_node(root),
            blackboard=blackboard,
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize the full behavior tree snapshot."""
        nodes = self.root.flatten()
        status_counts = _status_counts(nodes)
        return {
            "root_status": self.root.status,
            "nodes": [node.to_dict() for node in nodes],
            "status_counts": status_counts,
            "running_nodes": _paths_with_status(nodes, "RUNNING"),
            "success_nodes": _paths_with_status(nodes, "SUCCESS"),
            "failure_nodes": _paths_with_status(nodes, "FAILURE"),
            "invalid_nodes": _paths_with_status(nodes, "INVALID"),
            "blackboard": self.blackboard,
        }


def _status_counts(nodes: tuple[RobotBehaviorNodeSnapshot, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in nodes:
        counts[node.status] = counts.get(node.status, 0) + 1
    return counts


def _paths_with_status(
    nodes: tuple[RobotBehaviorNodeSnapshot, ...],
    status: str,
) -> list[str]:
    return [node.path for node in nodes if node.status == status]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def build_robot_tree(blackboard: RobotBlackboard | None = None) -> RobotBehaviorTree:
    """Build the top-level RobotRuntime behavior tree."""
    resolved_blackboard = blackboard or RobotBlackboard.create()
    root = py_trees.composites.Sequence(
        name="RobotRuntimeRoot",
        memory=False,
        children=[
            _SafetyGate(resolved_blackboard),
            _LifecycleGate(resolved_blackboard),
            _TurnCoordinator(resolved_blackboard),
        ],
    )
    return RobotBehaviorTree(
        blackboard=resolved_blackboard,
        tree=py_trees.trees.BehaviourTree(root),
    )


__all__ = [
    "RobotBehaviorNodeSnapshot",
    "RobotBehaviorTree",
    "RobotBehaviorTreeSnapshot",
    "build_robot_tree",
]
