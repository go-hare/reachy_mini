"""Tests for v4 SpeechPresenter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdk_fakes import assistant_message, result_message  # noqa: E402

from reachy_mini.pipeline.frames import (
    InterruptFrame,
    SDKMessageFrame,
    SpeechPresenterFrame,
    TTSStopFrame,
)
from reachy_mini.pipeline.speech_presenter import SpeechPresenter


@pytest.mark.asyncio
async def test_speech_presenter_splits_sdk_assistant_text() -> None:
    """SDK AssistantMessage text is split into final-marked TTS chunks."""
    presenter = SpeechPresenter(max_chars=20, style={"voice": "zf_001"})

    frames = await presenter.process(
        SDKMessageFrame(
            message=assistant_message("你好。我在这里。"),
            turn_id="t1",
        )
    )

    assert all(isinstance(frame, SpeechPresenterFrame) for frame in frames)
    assert [frame.text for frame in frames] == ["你好。", "我在这里。"]
    assert frames[-1].is_final is True
    assert frames[0].style["voice"] == "zf_001"


@pytest.mark.asyncio
async def test_speech_presenter_skips_non_text_sdk_messages() -> None:
    """Non-assistant SDK messages do not reach TTS."""
    presenter = SpeechPresenter()

    frames = await presenter.process(
        SDKMessageFrame(message=result_message(), turn_id="t1")
    )

    assert frames == []


@pytest.mark.asyncio
async def test_speech_presenter_interrupt_emits_stop_frame() -> None:
    """Speech interrupt becomes a TTSStopFrame."""
    presenter = SpeechPresenter()

    frames = await presenter.process(
        InterruptFrame(scope="speech", turn_id="t1", reason="barge_in")
    )

    assert len(frames) == 1
    assert isinstance(frames[0], TTSStopFrame)
    assert frames[0].reason == "barge_in"
