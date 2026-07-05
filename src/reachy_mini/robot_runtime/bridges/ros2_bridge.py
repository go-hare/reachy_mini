"""ROS2 bridge contract helpers without importing ROS at runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from reachy_mini.robot_runtime.contracts import RobotCommand, RobotEvent, RobotState


@dataclass(frozen=True, slots=True)
class ROS2Bridge:
    """Serialize RobotRuntime contracts for a future ROS2 node bridge."""

    namespace: str = "/reachy_mini"

    def state_message(self, state: RobotState) -> dict[str, Any]:
        """Return a ROS2-friendly state payload."""
        return {
            "topic": f"{self.namespace}/robot_state",
            "payload": state.to_dict(),
        }

    def command_message(self, command: RobotCommand) -> dict[str, Any]:
        """Return a ROS2-friendly command payload."""
        return {
            "topic": f"{self.namespace}/robot_command",
            "payload": command.to_dict(),
        }

    def event_message(self, event: RobotEvent) -> dict[str, Any]:
        """Return a ROS2-friendly event payload."""
        return {
            "topic": f"{self.namespace}/robot_event",
            "payload": event.to_dict(),
        }


__all__ = ["ROS2Bridge"]
