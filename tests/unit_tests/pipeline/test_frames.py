"""Tests for v4 pipeline frame contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from reachy_mini.action_runtime import ActionResult, ActionSpec
from reachy_mini.pipeline.frames import ActionResultFrame, BrainReplyFrame


def test_brain_reply_frame_is_immutable() -> None:
    """Frames are frozen dataclasses."""
    frame = BrainReplyFrame(reply_text="hi", turn_id="t1")

    with pytest.raises(FrozenInstanceError):
        frame.reply_text = "changed"  # type: ignore[misc]


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
    assert frame.status == "ok"
    assert frame.duration_ms == 12


def test_brain_reply_frame_keeps_action_specs() -> None:
    """BrainReplyFrame carries serializable ActionSpec intents."""
    spec = ActionSpec(name="nod", owner_id="main-agent")
    frame = BrainReplyFrame(reply_text="hi", actions=[spec], turn_id="t1")

    assert frame.actions == [spec]
