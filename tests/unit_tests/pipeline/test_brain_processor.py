"""Tests for v4 BrainProcessor."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdk_fakes import run_action_ok  # noqa: E402

from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.pipeline.brain_processor import BrainProcessor
from reachy_mini.pipeline.frames import (
    BrowserInputFrame,
    InterruptFrame,
    SDKMessageFrame,
    SpeechActivityFrame,
    TranscriptionFrame,
    TTSAudioFrame,
    VisionEventFrame,
)
from reachy_mini.reachy_brain.agent import BrainAgent
from reachy_mini.reachy_brain.config import (
    AgentConfig,
    ModelConfig,
    SpeechConfig,
    SpeechInputConfig,
    VisionConfig,
)
from reachy_mini.reachy_brain.offline_sdk_client import OfflineSDKClient


def _agent() -> BrainAgent:
    config = AgentConfig(
        model=ModelConfig(provider="mock", model="mock"),
        speech=SpeechConfig(enabled=True),
        speech_input=SpeechInputConfig(enabled=True),
        vision=VisionConfig(),
        extras={},
    )
    return BrainAgent(
        config=config,
        registry=create_builtin_registry(),
        run_action=run_action_ok,
        client_factory=lambda options: OfflineSDKClient(options),
    )


@pytest.mark.asyncio
async def test_final_transcription_triggers_sdk_message_stream() -> None:
    """Final STT frames run the SDK-backed Brain."""
    processor = BrainProcessor(_agent())

    frames = await processor.process(
        TranscriptionFrame(text="你好", is_final=True, turn_id="t1", lang="zh")
    )

    sdk_frames = [frame for frame in frames if isinstance(frame, SDKMessageFrame)]
    assert sdk_frames
    assert sdk_frames[0].turn_id == "t1"
    assert type(sdk_frames[0].message).__name__ == "AssistantMessage"


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

    assert isinstance(frames[0], SDKMessageFrame)
    assert frames[0].turn_id == "web:t1"


@pytest.mark.asyncio
async def test_speech_activity_start_during_tts_emits_interrupt() -> None:
    """Barge-in while TTS is active emits a speech interrupt."""
    processor = BrainProcessor(_agent())
    await processor.process(
        TTSAudioFrame(pcm=b"audio", sample_rate=24000, turn_id="t1", is_final=False)
    )

    frames = await processor.process(SpeechActivityFrame(state="start", ts_ms=123))

    assert len(frames) == 1
    assert isinstance(frames[0], InterruptFrame)
    assert frames[0].scope == "speech"
    assert frames[0].reason == "barge_in"
