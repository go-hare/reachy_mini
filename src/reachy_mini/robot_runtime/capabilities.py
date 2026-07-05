"""Capability inventory and matching for RobotRuntime adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from reachy_mini.robot_runtime.contracts import (
    ALLOWED_CHANNELS,
    Capability,
    make_robot_id,
)


@dataclass(frozen=True, slots=True)
class CapabilityQuery:
    """Body-agnostic capability query used by resolver and scheduler."""

    semantic_tags: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    modality: str = ""
    embodiment: str = ""
    affect: str | None = None
    intensity: float | None = None
    adapter_id: str | None = None
    constraints: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate requested channels."""
        invalid = sorted(set(self.channels) - ALLOWED_CHANNELS)
        if invalid:
            raise ValueError(f"channels contains unsupported channels: {invalid}")
        if self.intensity is not None and not 0.0 <= float(self.intensity) <= 1.0:
            raise ValueError("intensity must be between 0.0 and 1.0")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityQuery":
        """Create a query from a behavior step query dictionary."""
        semantic_tags = data.get("semantic_tags") or data.get("tags") or ()
        channels = data.get("channels") or ()
        return cls(
            semantic_tags=tuple(str(item) for item in semantic_tags),
            channels=tuple(str(item) for item in channels),
            modality=str(data.get("modality", "")),
            embodiment=str(data.get("embodiment", "")),
            affect=data.get("affect"),
            intensity=data.get("intensity"),
            adapter_id=data.get("adapter_id"),
            constraints=dict(data.get("constraints") or {}),
        )


@dataclass(frozen=True, slots=True)
class CapabilityMatch:
    """Scored capability match."""

    capability: Capability
    score: float
    reasons: tuple[str, ...] = ()


@dataclass(slots=True)
class CapabilityRegistry:
    """In-memory capability inventory with deterministic matching."""

    _items: dict[str, Capability] = field(default_factory=dict)
    revision: str = field(default_factory=lambda: make_robot_id("caprev"))

    def register(self, capability: Capability) -> None:
        """Register or replace one capability."""
        self._items[capability.capability_id] = capability
        self.revision = make_robot_id("caprev")

    def register_many(self, capabilities: list[Capability]) -> None:
        """Register multiple capabilities."""
        for capability in capabilities:
            self._items[capability.capability_id] = capability
        if capabilities:
            self.revision = make_robot_id("caprev")

    def remove(self, capability_id: str) -> None:
        """Remove a capability if present."""
        if capability_id in self._items:
            self._items.pop(capability_id)
            self.revision = make_robot_id("caprev")

    def list(self, *, adapter_id: str | None = None) -> list[Capability]:
        """List capabilities, optionally scoped to one adapter."""
        capabilities = list(self._items.values())
        if adapter_id is not None:
            capabilities = [item for item in capabilities if item.adapter_id == adapter_id]
        return sorted(capabilities, key=lambda item: item.capability_id)

    def query(self, query: CapabilityQuery) -> list[CapabilityMatch]:
        """Return matching capabilities sorted by descending score."""
        matches = [
            match
            for capability in self._items.values()
            if (match := self._match(capability, query)) is not None
        ]
        return sorted(
            matches,
            key=lambda item: (-item.score, item.capability.capability_id),
        )

    def _match(
        self, capability: Capability, query: CapabilityQuery
    ) -> CapabilityMatch | None:
        reasons: list[str] = []
        if query.adapter_id and capability.adapter_id != query.adapter_id:
            return None
        if query.embodiment and capability.embodiment != query.embodiment:
            return None
        if query.modality and capability.modality != query.modality:
            return None
        if query.channels and not set(query.channels).issubset(capability.channels):
            return None
        if query.affect and capability.affect_range and query.affect not in capability.affect_range:
            return None
        if query.intensity is not None:
            lo, hi = capability.intensity_range
            if not float(lo) <= float(query.intensity) <= float(hi):
                return None

        score = capability.confidence
        query_tags = set(query.semantic_tags)
        if query_tags:
            matched_tags = query_tags & set(capability.semantic_tags)
            if not matched_tags:
                return None
            score += len(matched_tags) / len(query_tags)
            reasons.append(f"matched_tags={','.join(sorted(matched_tags))}")
        if query.channels:
            score += 0.1 * len(query.channels)
            reasons.append("channels_matched")
        return CapabilityMatch(
            capability=capability,
            score=round(score, 4),
            reasons=tuple(reasons),
        )


__all__ = ["CapabilityMatch", "CapabilityQuery", "CapabilityRegistry"]
