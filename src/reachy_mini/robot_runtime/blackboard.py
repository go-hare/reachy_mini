"""py_trees blackboard wrapper for RobotRuntime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import py_trees

from reachy_mini.robot_runtime.contracts import EmbodiedIntent, RobotState

_BLACKBOARD_KEYS = (
    "robot_state",
    "active_turn_id",
    "pending_intents",
    "active_plan",
    "speech_state",
    "vision_state",
    "attention_target",
    "safety_status",
    "adapter_capabilities",
    "active_commands",
    "interrupt_request",
)


@dataclass(slots=True)
class RobotBlackboard:
    """Typed facade over a py_trees blackboard client."""

    client: py_trees.blackboard.Client

    @classmethod
    def create(cls, *, name: str = "RobotRuntime") -> "RobotBlackboard":
        """Create and initialize a RobotRuntime blackboard."""
        client = py_trees.blackboard.Client(name=name)
        for key in _BLACKBOARD_KEYS:
            client.register_key(key=key, access=py_trees.common.Access.WRITE)
            client.register_key(key=key, access=py_trees.common.Access.READ)
        board = cls(client=client)
        board.initialize()
        return board

    def initialize(self) -> None:
        """Initialize every public blackboard key with safe defaults."""
        self.client.set("robot_state", RobotState())
        self.client.set("active_turn_id", None)
        self.client.set("pending_intents", [])
        self.client.set("active_plan", None)
        self.client.set("speech_state", "idle")
        self.client.set("vision_state", {})
        self.client.set("attention_target", None)
        self.client.set("safety_status", {})
        self.client.set("adapter_capabilities", {})
        self.client.set("active_commands", [])
        self.client.set("interrupt_request", None)

    def update_robot_state(self, state: RobotState) -> None:
        """Set the latest RobotState snapshot."""
        self.client.set("robot_state", state)
        self.client.set("active_turn_id", state.active_turn_id)
        self.client.set("speech_state", state.speech_state)
        self.client.set("attention_target", state.attention_target)
        self.client.set("active_commands", list(state.active_commands))

    def enqueue_intent(self, intent: EmbodiedIntent) -> None:
        """Append one pending intent for behavior tree consumption."""
        pending = list(self.client.get("pending_intents") or [])
        pending.append(intent)
        self.client.set("pending_intents", pending)

    def pop_next_intent(self) -> EmbodiedIntent | None:
        """Pop the highest-priority pending intent."""
        pending = list(self.client.get("pending_intents") or [])
        if not pending:
            return None
        pending.sort(key=lambda item: item.priority, reverse=True)
        intent = pending.pop(0)
        self.client.set("pending_intents", pending)
        return intent

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-friendly debugging snapshot."""
        return {key: self.client.get(key) for key in _BLACKBOARD_KEYS}


__all__ = ["RobotBlackboard"]
