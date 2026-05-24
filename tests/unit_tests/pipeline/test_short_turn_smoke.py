"""SC-1 short-turn smoke test for the v4 mock pipeline."""

from __future__ import annotations

import time
from typing import Any

import pytest

from reachy_mini.action_runtime import ActionExecutor
from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.reachy_brain.agent import BrainAgent
from reachy_mini.reachy_brain.config import AgentConfig, ModelConfig, SpeechConfig, SpeechInputConfig, VisionConfig
from reachy_mini.pipeline.action_dispatcher import ActionDispatcher
from reachy_mini.pipeline.brain_processor import BrainProcessor
from reachy_mini.pipeline.frames import (
    ActionResultFrame,
    ActionSpecFrame,
    BrainReplyFrame,
    SpeechPresenterFrame,
    TranscriptionFrame,
    TTSAudioFrame,
)
from reachy_mini.pipeline.speech_presenter import SpeechPresenter
from reachy_mini.pipeline.tts_kokoro import KokoroAdapter


class FakeMini:
    def goto_target(self, **kwargs: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_short_turn_smoke() -> None:
    """Final transcription produces reply, speech frames, TTS, and action result."""
    config = AgentConfig(
        model=ModelConfig(provider="mock", model="mock"),
        speech=SpeechConfig(enabled=True),
        speech_input=SpeechInputConfig(enabled=True),
        vision=VisionConfig(),
        extras={},
    )
    registry = create_builtin_registry()
    brain = BrainProcessor(BrainAgent(config=config, registry=registry))
    speech = SpeechPresenter()
    tts = KokoroAdapter(config.speech)
    dispatcher = ActionDispatcher(ActionExecutor(registry=registry, mini=FakeMini()))

    start = time.monotonic()
    reply_frames = await brain.process(
        TranscriptionFrame(text="你好", is_final=True, turn_id="T1", lang="zh")
    )
    reply_at = time.monotonic()
    brain_reply = reply_frames[0]
    speech_frames = await speech.process(brain_reply)
    tts_frames = []
    for speech_frame in speech_frames:
        tts_frames.extend(await tts.process(speech_frame))
    spec_frames = await dispatcher.process(brain_reply)
    result_frames = []
    for spec_frame in spec_frames:
        result_frames.extend(await dispatcher.process(spec_frame))
    done_at = time.monotonic()

    assert isinstance(brain_reply, BrainReplyFrame)
    assert brain_reply.turn_id == "T1"
    assert brain_reply.reply_text
    assert brain_reply.actions[0].name in {"nod", "look_at", "play_emotion"}
    assert all(isinstance(frame, SpeechPresenterFrame) for frame in speech_frames)
    assert speech_frames[-1].is_final is True
    assert all(isinstance(frame, TTSAudioFrame) for frame in tts_frames)
    assert tts_frames[-1].is_final is True
    assert isinstance(spec_frames[0], ActionSpecFrame)
    assert isinstance(result_frames[0], ActionResultFrame)
    assert result_frames[0].status == "ok"
    assert (reply_at - start) <= 0.6
    assert (done_at - start) <= 2.0
