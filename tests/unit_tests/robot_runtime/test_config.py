"""Tests for RobotRuntime config parsing."""

from __future__ import annotations

import pytest

from reachy_mini.robot_runtime.config import RobotRuntimeConfig
from reachy_mini.robot_runtime.contracts import RuntimeMode


def test_default_config_is_disabled_avatar_only() -> None:
    """RobotRuntime is opt-in by default."""
    config = RobotRuntimeConfig()

    assert config.enabled is False
    assert config.mode is RuntimeMode.AVATAR_ONLY
    assert config.tick_hz == 30
    assert config.legacy_live2d_tools_enabled is False


def test_enabled_runtime_defaults_to_intent_only_tools() -> None:
    """RobotRuntime opt-in defaults to hiding concrete Live2D tools."""
    config = RobotRuntimeConfig.from_records(
        [{"kind": "robot_runtime", "enabled": True}]
    )

    assert config.enabled is True
    assert config.legacy_live2d_tools_enabled is False


def test_enabled_runtime_can_explicitly_keep_legacy_live2d_tools() -> None:
    """Legacy Live2D tools require an explicit compatibility flag."""
    config = RobotRuntimeConfig.from_records(
        [
            {
                "kind": "robot_runtime",
                "enabled": True,
                "legacy_live2d_tools_enabled": True,
            }
        ]
    )

    assert config.enabled is True
    assert config.legacy_live2d_tools_enabled is True


def test_config_from_profile_records() -> None:
    """Profile JSONL records map to typed runtime and adapter config."""
    config = RobotRuntimeConfig.from_records(
        [
            {
                "kind": "robot_runtime",
                "enabled": True,
                "mode": "hybrid",
                "tick_hz": 25,
                "adapters": ["live2d", "mujoco"],
                "safety_profile": "simulation",
                "legacy_live2d_tools_enabled": False,
            },
            {
                "kind": "robot_adapter",
                "adapter": "live2d",
                "enabled": True,
                "capabilities_from": "avatar.config.json",
            },
            {
                "kind": "robot_safety_profile",
                "name": "simulation",
                "max_command_hz": 30,
            },
        ]
    )

    assert config.enabled is True
    assert config.mode is RuntimeMode.HYBRID
    assert config.tick_hz == 25
    assert config.adapters == ("live2d", "mujoco")
    assert config.legacy_live2d_tools_enabled is False
    assert config.adapter_configs["live2d"].capabilities_from == "avatar.config.json"
    assert config.safety_profiles["simulation"].limits["max_command_hz"] == 30


def test_config_rejects_invalid_tick_rate() -> None:
    """A non-positive tick rate is invalid."""
    with pytest.raises(ValueError, match="tick_hz"):
        RobotRuntimeConfig(tick_hz=0)
