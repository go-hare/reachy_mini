"""SC-3 barge-in smoke test for the v4 mock pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdk_fakes import run_action_ok  # noqa: E402

from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.reachy_brain.agent import BrainAgent
from reachy_mini.reachy_brain.config import AgentConfig, ModelConfig, SpeechConfig, SpeechInputConfig, VisionConfig
from reachy_mini.reachy_brain.offline_sdk_client import OfflineSDKClient
from reachy_mini.pipeline.brain_processor import BrainProcessor
from reachy_mini.pipeline.frames import InterruptFrame, SpeechActivityFrame, TranscriptionFrame, TTSAudioFrame, TTSStopFrame
from reachy_mini.pipeline.speech_presenter import SpeechPresenter


def _processor() -> BrainProcessor:
    config = AgentConfig(
        model=ModelConfig(provider="mock", model="mock"),
        speech=SpeechConfig(enabled=True),
        speech_input=SpeechInputConfig(enabled=True),
        vision=VisionConfig(),
        extras={},
    )
    return BrainProcessor(
        BrainAgent(
            config=config,
            registry=create_builtin_registry(),
            run_action=run_action_ok,
            client_factory=lambda options: OfflineSDKClient(options),
        )
    )


@pytest.mark.asyncio
async def test_barge_in_stops_speech_and_allows_new_turn() -> None:
    """Speech activity during TTS emits interrupt and next turn has a new id."""
    processor = _processor()
    presenter = SpeechPresenter()
    await processor.process(TTSAudioFrame(pcm=b"chunk", sample_rate=24000, turn_id="T1", is_final=False))

    interrupts = await processor.process(SpeechActivityFrame(state="start", ts_ms=200))
    stop_frames = await presenter.process(interrupts[0])
    new_reply = await processor.process(
        TranscriptionFrame(text="等等", is_final=True, turn_id="T2", lang="zh")
    )

    assert isinstance(interrupts[0], InterruptFrame)
    assert interrupts[0].scope == "speech"
    assert isinstance(stop_frames[0], TTSStopFrame)
    assert new_reply[0].turn_id == "T2"
    assert new_reply[0].turn_id != "T1"
