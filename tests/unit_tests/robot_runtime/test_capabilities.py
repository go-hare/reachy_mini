"""Tests for RobotRuntime capability inventory."""

from __future__ import annotations

import pytest

from reachy_mini.robot_runtime.capabilities import CapabilityQuery, CapabilityRegistry
from reachy_mini.robot_runtime.contracts import Capability


def test_registry_matches_semantic_tags_and_channels() -> None:
    """Capability matching is semantic and body-agnostic."""

    registry = CapabilityRegistry()
    registry.register_many(
        [
            Capability(
                capability_id="live2d_greet",
                adapter_id="live2d",
                embodiment="live2d",
                modality="motion",
                channels=["face", "gesture"],
                semantic_tags=["greet", "positive"],
                confidence=0.8,
            ),
            Capability(
                capability_id="reachy_idle",
                adapter_id="reachy",
                embodiment="reachy",
                modality="motion",
                channels=["head"],
                semantic_tags=["idle"],
            ),
        ]
    )

    matches = registry.query(
        CapabilityQuery(
            semantic_tags=("greet",),
            channels=("face",),
            modality="motion",
        )
    )

    assert [match.capability.capability_id for match in matches] == ["live2d_greet"]
    assert matches[0].score > 1.0
    assert "matched_tags=greet" in matches[0].reasons


def test_query_rejects_unknown_channels() -> None:
    """Capability queries use controlled channels."""

    with pytest.raises(ValueError, match="unsupported channels"):
        CapabilityQuery(channels=("motion3.json",))
