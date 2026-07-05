"""Adapter factory for RobotRuntime profile configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from reachy_mini.robot_runtime.adapters.base import RobotAdapter
from reachy_mini.robot_runtime.adapters.mujoco import MujocoAdapter
from reachy_mini.robot_runtime.adapters.reachy import ReachyAdapter
from reachy_mini.robot_runtime.config import RobotRuntimeConfig
from reachy_mini.runtime.live2d_avatar import Live2DCapabilities


@dataclass(frozen=True, slots=True)
class AdapterBuildResult:
    """Result of building adapters from one profile."""

    adapters: list[RobotAdapter] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)


def build_profile_adapters(
    config: RobotRuntimeConfig,
    *,
    live2d_capabilities: Live2DCapabilities | None = None,
    mini: Any | None = None,
) -> AdapterBuildResult:
    """Build enabled adapters from RobotRuntime profile config."""
    adapters: list[RobotAdapter] = []
    skipped: dict[str, str] = {}
    for adapter_name in _adapter_names(config, live2d_capabilities=live2d_capabilities):
        adapter_config = config.adapter_configs.get(adapter_name)
        if adapter_config is not None and not adapter_config.enabled:
            skipped[adapter_name] = "disabled"
            continue
        if adapter_name == "live2d":
            if live2d_capabilities is None:
                skipped[adapter_name] = "missing_live2d_capabilities"
                continue
            from reachy_mini.robot_runtime.adapters.live2d import Live2DAdapter

            adapters.append(Live2DAdapter(live2d_capabilities))
        elif adapter_name == "mujoco":
            adapters.append(MujocoAdapter())
        elif adapter_name == "avatar3d":
            from reachy_mini.robot_runtime.adapters.avatar3d import Avatar3DAdapter

            adapters.append(Avatar3DAdapter())
        elif adapter_name == "ros2":
            from reachy_mini.robot_runtime.adapters.ros2 import ROS2Adapter

            adapters.append(ROS2Adapter())
        elif adapter_name == "reachy":
            adapters.append(
                ReachyAdapter(
                    mini=mini,
                )
            )
        else:
            skipped[adapter_name] = "unknown_adapter"
    return AdapterBuildResult(adapters=adapters, skipped=skipped)


def adapter_config_options(
    config: RobotRuntimeConfig,
    adapter_id: str,
) -> dict[str, object]:
    """Return adapter options including safety profile defaults."""
    adapter_config = config.adapter_configs.get(adapter_id)
    options: dict[str, object] = {
        "safety_profile": config.safety_profile,
    }
    if adapter_config is not None:
        options.update(adapter_config.options)
        if adapter_config.capabilities_from:
            options["capabilities_from"] = adapter_config.capabilities_from
    return options


def _adapter_names(
    config: RobotRuntimeConfig,
    *,
    live2d_capabilities: Live2DCapabilities | None,
) -> tuple[str, ...]:
    if config.adapters:
        return config.adapters
    configured = tuple(config.adapter_configs)
    if configured:
        return configured
    if live2d_capabilities is not None:
        return ("live2d",)
    return ()


__all__ = ["AdapterBuildResult", "adapter_config_options", "build_profile_adapters"]
