"""Tests for RobotRuntime lifecycle manager."""

from __future__ import annotations

import pytest

from reachy_mini.robot_runtime import LifecycleState
from reachy_mini.robot_runtime.errors import LifecycleTransitionError
from reachy_mini.robot_runtime.lifecycle import LifecycleManager


def test_lifecycle_happy_path() -> None:
    """Lifecycle manager records valid transitions."""

    manager = LifecycleManager()

    manager.configure()
    manager.mark_inactive()
    manager.activate()
    manager.mark_active()
    manager.deactivate()
    manager.mark_inactive()

    assert manager.state is LifecycleState.INACTIVE
    assert [transition.target for transition in manager.history] == [
        LifecycleState.CONFIGURING,
        LifecycleState.INACTIVE,
        LifecycleState.ACTIVATING,
        LifecycleState.ACTIVE,
        LifecycleState.DEACTIVATING,
        LifecycleState.INACTIVE,
    ]


def test_lifecycle_rejects_invalid_transition() -> None:
    """Skipping lifecycle states is not allowed."""

    manager = LifecycleManager()

    with pytest.raises(LifecycleTransitionError, match="Cannot transition"):
        manager.mark_active()
