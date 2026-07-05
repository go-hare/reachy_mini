"""LeRobot dataset bridge helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from reachy_mini.robot_runtime.contracts import AdapterResult, RobotCommand, RobotState


@dataclass(frozen=True, slots=True)
class LeRobotBridge:
    """Convert RobotRuntime contracts into offline policy/dataset rows."""

    dataset_version: str = "robot_runtime_v1"

    def command_row(
        self,
        *,
        state: RobotState,
        command: RobotCommand,
        result: AdapterResult | None = None,
    ) -> dict[str, Any]:
        """Build one dataset row from state, action, and optional result."""
        return {
            "dataset_version": self.dataset_version,
            "observation": state.to_dict(),
            "action": command.to_dict(),
            "result": result.to_dict() if result is not None else None,
        }


__all__ = ["LeRobotBridge"]
