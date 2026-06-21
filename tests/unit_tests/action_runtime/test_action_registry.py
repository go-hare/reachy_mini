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
from reachy_mini.action_runtime.library import create_live2d_registry
from reachy_mini.action_runtime.library.play_emotion import resolve_emotion_move_name
from reachy_mini.runtime.live2d_avatar import Live2DCapabilities, Live2DNativeAction


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


def test_play_emotion_resolves_semantic_aliases_to_recorded_moves() -> None:
    """LLM-friendly emotion names map to concrete recorded move names."""

    available = ["cheerful1", "enthusiastic1", "sad1"]

    assert resolve_emotion_move_name("happy", available) == "cheerful1"
    assert resolve_emotion_move_name("excited", available) == "enthusiastic1"
    assert resolve_emotion_move_name("sad1", available) == "sad1"


def test_live2d_registry_replaces_robot_actions() -> None:
    """Live2D mode exposes native Live2D actions only."""
    registry = create_live2d_registry(
        Live2DCapabilities(
            model_name="IceGirl",
            root_url="/static/assets/live2d/IceGirl",
            vtube_file="IceGirl.vtube.json",
            model_file="IceGirl.model3.json",
            idle_motion="DaiJi",
            motions=("DaiJi", "HuiShou"),
            expressions=("惊讶", "脸红"),
            motion_details=(
                Live2DNativeAction(
                    name="DaiJi",
                    file_name="DaiJi.motion3.json",
                    aliases=("待机", "idle"),
                ),
                Live2DNativeAction(
                    name="HuiShou",
                    file_name="HuiShou.motion3.json",
                    aliases=("HuiShou", "挥手", "举手", "招手", "抬手"),
                    parameter_ids=("Param58", "Param59"),
                    parameter_labels=("挥手",),
                    duration_s=7,
                ),
            ),
            expression_details=(
                Live2DNativeAction(
                    name="惊讶",
                    file_name="惊讶.exp3.json",
                    aliases=("惊讶", "吃惊", "震惊"),
                    parameter_ids=("JingYa",),
                    parameter_labels=("惊讶",),
                ),
                Live2DNativeAction(
                    name="脸红",
                    file_name="脸红.exp3.json",
                    aliases=("脸红", "害羞"),
                    parameter_ids=("Param31",),
                    parameter_labels=("脸红",),
                ),
            ),
        )
    )

    names = {metadata.name for metadata in registry.list_metadata()}

    assert names == {
        "live2d_motion_daiji",
        "live2d_motion_huishou",
        "live2d_expression_jing_ya",
        "live2d_expression_lian_hong",
    }
    for metadata in registry.list_metadata():
        assert metadata.parameter_schema == {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        }
    wave_metadata = registry.get_metadata("live2d_motion_huishou")
    assert "HuiShou.motion3.json" in wave_metadata.description
    assert "挥手" in wave_metadata.description
    assert "举手" in wave_metadata.description
    assert "招手" in wave_metadata.description
    assert "Param58(挥手)" in wave_metadata.description
    assert "already exists" in wave_metadata.description
    assert wave_metadata.default_duration_s == 7

    surprise_metadata = registry.get_metadata("live2d_expression_jing_ya")
    assert "惊讶.exp3.json" in surprise_metadata.description
    assert "吃惊" in surprise_metadata.description
