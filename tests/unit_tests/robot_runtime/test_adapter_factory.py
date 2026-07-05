"""Tests for RobotRuntime adapter factory."""

from __future__ import annotations

from reachy_mini.robot_runtime.adapters.factory import (
    adapter_config_options,
    build_profile_adapters,
)
from reachy_mini.robot_runtime.config import RobotRuntimeConfig


def test_factory_builds_configured_mujoco_reachy_avatar3d_and_ros2_adapters() -> None:
    """Profile adapter names produce concrete adapter instances."""
    config = RobotRuntimeConfig.from_records(
        [
            {
                "kind": "robot_runtime",
                "enabled": True,
                "adapters": ["mujoco", "reachy", "avatar3d", "ros2"],
                "safety_profile": "simulation",
            },
            {
                "kind": "robot_adapter",
                "adapter": "reachy",
                "enabled": True,
                "dry_run": True,
            },
        ]
    )

    result = build_profile_adapters(config)

    assert [adapter.adapter_id for adapter in result.adapters] == [
        "mujoco",
        "reachy",
        "avatar3d",
        "ros2",
    ]
    assert result.skipped == {}
    assert adapter_config_options(config, "reachy") == {
        "safety_profile": "simulation",
        "dry_run": True,
    }


def test_factory_skips_disabled_and_unknown_adapters() -> None:
    """Disabled and unknown adapters are reported without raising."""
    config = RobotRuntimeConfig.from_records(
        [
            {
                "kind": "robot_runtime",
                "enabled": True,
                "adapters": ["live2d", "unknown"],
            },
            {
                "kind": "robot_adapter",
                "adapter": "live2d",
                "enabled": False,
            },
        ]
    )

    result = build_profile_adapters(config)

    assert result.adapters == []
    assert result.skipped == {
        "live2d": "disabled",
        "unknown": "unknown_adapter",
    }
