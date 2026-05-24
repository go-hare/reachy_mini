"""Tests for v4 SpeechPresenter."""

from __future__ import annotations

import pytest

from reachy_mini.pipeline.frames import BrainReplyFrame, InterruptFrame, SpeechPresenterFrame, TTSStopFrame
from reachy_mini.pipeline.speech_presenter import SpeechPresenter


@pytest.mark.asyncio
async def test_speech_presenter_splits_reply_text() -> None:
    """Reply text is split into final-marked TTS chunks."""
    presenter = SpeechPresenter(max_chars=20)

    frames = await presenter.process(
        BrainReplyFrame(
            reply_text="你好。我在这里。",
            speech_style={"voice": "zf_001"},
            turn_id="t1",
        )
    )

    assert all(isinstance(frame, SpeechPresenterFrame) for frame in frames)
    assert [frame.text for frame in frames] == ["你好。", "我在这里。"]
    assert frames[-1].is_final is True
    assert frames[0].style["voice"] == "zf_001"


@pytest.mark.asyncio
async def test_speech_presenter_skips_empty_reply() -> None:
    """Empty replies do not reach TTS."""
    presenter = SpeechPresenter()

    frames = await presenter.process(BrainReplyFrame(reply_text="", turn_id="t1"))

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
