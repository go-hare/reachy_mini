"""Tests for the v4 action registry."""

from __future__ import annotations

import pytest

from reachy_mini.action_runtime import (
    ActionParamError,
    ActionSpec,
    DuplicateActionError,
    UnknownActionError,
)
from reachy_mini.action_runtime.library import create_builtin_registry


def test_builtin_registry_lists_metadata_without_robot() -> None:
    """Built-in action metadata is readable without a robot connection."""
    registry = create_builtin_registry()

    names = {metadata.name for metadata in registry.list_metadata()}

    assert {"look_at", "nod", "shake_head", "set_antenna", "play_emotion"} <= names


def test_registry_rejects_unknown_action() -> None:
    """Unknown action names fail before any SDK call."""
    registry = create_builtin_registry()

    with pytest.raises(UnknownActionError):
        registry.build(ActionSpec(name="missing", owner_id="manual-test"))


def test_registry_validates_parameters() -> None:
    """Action specs are validated against their JSON schema."""
    registry = create_builtin_registry()

    with pytest.raises(ActionParamError):
        registry.build(
            ActionSpec(
                name="nod",
                params={"cycles": 99},
                owner_id="manual-test",
            )
        )


def test_registry_rejects_duplicate_registration() -> None:
    """Action names are globally unique in one registry."""
    registry = create_builtin_registry()
    metadata = registry.get_metadata("nod")

    with pytest.raises(DuplicateActionError):
        registry.register(metadata, lambda spec: registry.build(spec))
