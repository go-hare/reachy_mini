"""Tests for quiet surface-presence handling in the movement manager."""

from __future__ import annotations

import numpy as np

from reachy_mini.runtime.moves import BreathingMove, MovementManager


class FakeRobot:
    """Small robot stub for movement-manager unit tests."""

    def get_current_head_pose(self) -> np.ndarray:
        return np.eye(4, dtype=np.float64)

    def get_current_joint_positions(self) -> tuple[list[float], list[float]]:
        return [0.0] * 7, [0.0, 0.0]

    def set_target(self, head, antennas, body_yaw) -> None:
        _ = (head, antennas, body_yaw)

    def goto_target(self, head, antennas, duration, body_yaw) -> None:
        _ = (head, antennas, duration, body_yaw)


def test_movement_manager_surface_active_blocks_idle_state() -> None:
    """A quiet surface phase should keep the body out of idle mode."""
    manager = MovementManager(FakeRobot())
    manager.state.last_activity_time = 0.0
    manager._shared_last_activity_time = 0.0
    manager._shared_surface_active = False

    assert manager.is_idle() is True

    manager._handle_command("set_surface_active", True, current_time=1.0)
    manager._publish_shared_state()

    assert manager.is_idle() is False


def test_movement_manager_surface_active_suppresses_and_cancels_breathing() -> None:
    """Surface presence should block idle breathing and stop it once attention arrives."""
    manager = MovementManager(FakeRobot())
    manager.state.last_activity_time = 0.0
    manager._surface_active = True

    manager._manage_breathing(current_time=1.0)

    assert not manager.move_queue
    assert manager.state.current_move is None
    assert manager._breathing_active is False

    manager._surface_active = False
    manager.state.last_activity_time = 0.0
    manager._manage_breathing(current_time=1.0)

    assert len(manager.move_queue) == 1
    assert isinstance(manager.move_queue[0], BreathingMove)

    manager._manage_move_queue(current_time=1.0)
    assert isinstance(manager.state.current_move, BreathingMove)

    manager._surface_active = True
    manager._manage_breathing(current_time=1.1)

    assert manager.state.current_move is None
    assert manager._breathing_active is False
