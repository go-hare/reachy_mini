"""Tests for v4 BrainProcessor."""

from __future__ import annotations

import pytest

from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.reachy_brain.agent import BrainAgent
from reachy_mini.reachy_brain.config import AgentConfig, ModelConfig, SpeechConfig, SpeechInputConfig, VisionConfig
from reachy_mini.pipeline.brain_processor import BrainProcessor
from reachy_mini.pipeline.frames import (
    BrainReplyFrame,
    BrowserInputFrame,
    InterruptFrame,
    SpeechActivityFrame,
    TranscriptionFrame,
    TTSAudioFrame,
    VisionEventFrame,
)


def _agent() -> BrainAgent:
    config = AgentConfig(
        model=ModelConfig(provider="mock", model="mock"),
        speech=SpeechConfig(enabled=True),
        speech_input=SpeechInputConfig(enabled=True),
        vision=VisionConfig(),
        extras={},
    )
    return BrainAgent(config=config, registry=create_builtin_registry())


@pytest.mark.asyncio
async def test_final_transcription_triggers_brain_reply() -> None:
    """Final STT frames run the Brain."""
    processor = BrainProcessor(_agent())

    frames = await processor.process(
        TranscriptionFrame(text="你好", is_final=True, turn_id="t1", lang="zh")
    )

    assert len(frames) == 1
    assert isinstance(frames[0], BrainReplyFrame)
    assert frames[0].turn_id == "t1"
    assert frames[0].reply_text
    assert frames[0].actions[0].name == "nod"


@pytest.mark.asyncio
async def test_partial_transcription_and_vision_are_buffered() -> None:
    """Non-final/context frames do not run the Brain immediately."""
    processor = BrainProcessor(_agent())

    partial = await processor.process(
        TranscriptionFrame(text="你", is_final=False, turn_id="t1", lang="zh")
    )
    vision = await processor.process(
        VisionEventFrame(event="face_detected", payload={"id": 1}, ts_ms=1)
    )

    assert partial == []
    assert vision == []
    assert len(processor.context_buffer) == 2


@pytest.mark.asyncio
async def test_browser_text_input_runs_brain() -> None:
    """Browser text input is equivalent to a final transcription."""
    processor = BrainProcessor(_agent())

    frames = await processor.process(
        BrowserInputFrame(
            kind="text",
            payload={"text": "你好", "turn_id": "web:t1"},
            session_id="web",
        )
    )

    assert isinstance(frames[0], BrainReplyFrame)
    assert frames[0].turn_id == "web:t1"


@pytest.mark.asyncio
async def test_speech_activity_start_during_tts_emits_interrupt() -> None:
    """Barge-in while TTS is active emits a speech interrupt."""
    processor = BrainProcessor(_agent())
    await processor.process(TTSAudioFrame(pcm=b"audio", sample_rate=24000, turn_id="t1", is_final=False))

    frames = await processor.process(SpeechActivityFrame(state="start", ts_ms=123))

    assert len(frames) == 1
    assert isinstance(frames[0], InterruptFrame)
    assert frames[0].scope == "speech"
    assert frames[0].reason == "barge_in"
