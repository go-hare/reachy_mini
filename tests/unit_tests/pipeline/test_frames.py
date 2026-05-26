"""Tests for v4 pipeline frame contracts."""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdk_fakes import assistant_message  # noqa: E402

from reachy_mini.action_runtime import ActionResult
from reachy_mini.pipeline.frames import ActionResultFrame, SDKMessageFrame


def test_sdk_message_frame_is_immutable() -> None:
    """SDKMessageFrame is a frozen dataclass."""
    frame = SDKMessageFrame(message=assistant_message("hi"), turn_id="t1")

    with pytest.raises(FrozenInstanceError):
        frame.turn_id = "changed"  # type: ignore[misc]


def test_action_result_frame_from_result() -> None:
    """ActionResult converts to the pipeline result frame."""
    result = ActionResult(
        request_id="r1",
        action_id="a1",
        name="nod",
        owner_id="main-agent",
        status="ok",
        duration_ms=12,
    )

    frame = ActionResultFrame.from_result(result)

    assert frame.request_id == "r1"
    assert frame.name == "nod"
    assert frame.owner_id == "main-agent"
    assert frame.status == "ok"
    assert frame.duration_ms == 12


def test_sdk_message_frame_keeps_native_message_object() -> None:
    """SDKMessageFrame wraps the SDK message without inventing a Brain schema."""
    message = assistant_message("hi")
    frame = SDKMessageFrame(message=message, turn_id="t1")

    assert frame.message is message
