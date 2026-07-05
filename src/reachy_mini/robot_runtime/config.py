"""Configuration models for the enterprise RobotRuntime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from reachy_mini.robot_runtime.contracts import RuntimeMode


@dataclass(frozen=True, slots=True)
class RobotAdapterConfig:
    """Configuration for one runtime adapter."""

    adapter: str
    enabled: bool = True
    capabilities_from: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "RobotAdapterConfig":
        """Build adapter config from a profile JSONL record."""
        known = {"kind", "adapter", "enabled", "capabilities_from"}
        return cls(
            adapter=str(record["adapter"]),
            enabled=bool(record.get("enabled", True)),
            capabilities_from=str(record.get("capabilities_from", "")),
            options={key: value for key, value in record.items() if key not in known},
        )


@dataclass(frozen=True, slots=True)
class SafetyProfileConfig:
    """Configuration for a named safety profile."""

    name: str
    limits: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RobotRuntimeConfig:
    """Top-level RobotRuntime config resolved from profile records."""

    enabled: bool = False
    mode: RuntimeMode = RuntimeMode.AVATAR_ONLY
    tick_hz: int = 30
    adapters: tuple[str, ...] = ()
    safety_profile: str = "avatar"
    intent_tools_enabled: bool = True
    legacy_live2d_tools_enabled: bool = False
    adapter_configs: dict[str, RobotAdapterConfig] = field(default_factory=dict)
    safety_profiles: dict[str, SafetyProfileConfig] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate bounded runtime options."""
        if not isinstance(self.mode, RuntimeMode):
            object.__setattr__(self, "mode", RuntimeMode(str(self.mode)))
        if int(self.tick_hz) <= 0:
            raise ValueError("tick_hz must be positive")
        object.__setattr__(self, "tick_hz", int(self.tick_hz))
        object.__setattr__(self, "adapters", tuple(str(item) for item in self.adapters))

    @classmethod
    def from_records(cls, records: list[dict[str, Any]]) -> "RobotRuntimeConfig":
        """Parse robot runtime config from profile JSONL records."""
        runtime_record: dict[str, Any] = {}
        adapter_configs: dict[str, RobotAdapterConfig] = {}
        safety_profiles: dict[str, SafetyProfileConfig] = {}
        for record in records:
            kind = record.get("kind")
            if kind == "robot_runtime":
                runtime_record = dict(record)
            elif kind == "robot_adapter":
                adapter = RobotAdapterConfig.from_record(record)
                adapter_configs[adapter.adapter] = adapter
            elif kind == "robot_safety_profile":
                name = str(record.get("name") or record.get("profile") or "")
                if not name:
                    raise ValueError("robot_safety_profile requires name")
                limits = dict(record)
                for key in ("kind", "name", "profile"):
                    limits.pop(key, None)
                safety_profiles[name] = SafetyProfileConfig(name=name, limits=limits)

        mode = RuntimeMode(str(runtime_record.get("mode", RuntimeMode.AVATAR_ONLY.value)))
        adapters = runtime_record.get("adapters", ())
        if isinstance(adapters, str):
            adapters = (adapters,)
        enabled = bool(runtime_record.get("enabled", False))
        return cls(
            enabled=enabled,
            mode=mode,
            tick_hz=int(runtime_record.get("tick_hz", 30)),
            adapters=tuple(str(adapter) for adapter in adapters),
            safety_profile=str(runtime_record.get("safety_profile", "avatar")),
            intent_tools_enabled=bool(runtime_record.get("intent_tools_enabled", True)),
            legacy_live2d_tools_enabled=bool(
                runtime_record.get("legacy_live2d_tools_enabled", False)
            ),
            adapter_configs=adapter_configs,
            safety_profiles=safety_profiles,
        )


__all__ = [
    "RobotAdapterConfig",
    "RobotRuntimeConfig",
    "SafetyProfileConfig",
]
