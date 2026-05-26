"""SC-1 short-turn smoke test for the SDK-first v4 mock pipeline."""

from __future__ import annotations

import time
from typing import Any

import pytest

from reachy_mini.action_runtime import ActionExecutor, ActionSpec
from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.pipeline.action_dispatcher import ActionDispatcher
from reachy_mini.pipeline.brain_processor import BrainProcessor
from reachy_mini.pipeline.frames import (
    ActionResultFrame,
    SDKMessageFrame,
    SpeechPresenterFrame,
    TranscriptionFrame,
    TTSAudioFrame,
)
from reachy_mini.pipeline.speech_presenter import SpeechPresenter
from reachy_mini.pipeline.tts_kokoro import KokoroAdapter
from reachy_mini.reachy_brain.agent import BrainAgent
from reachy_mini.reachy_brain.config import (
    AgentConfig,
    ModelConfig,
    SpeechConfig,
    SpeechInputConfig,
    VisionConfig,
)
from reachy_mini.reachy_brain.offline_sdk_client import OfflineSDKClient


class FakeMini:
    """Small fake robot for the action executor."""

    def goto_target(self, **kwargs: Any) -> None:
        return None

    def get_present_antenna_joint_positions(self) -> list[float]:
        return [0.0, 0.0]


@pytest.mark.asyncio
async def test_short_turn_smoke() -> None:
    """Final transcription produces SDK message, speech frames, TTS, and action result."""
    config = AgentConfig(
        model=ModelConfig(provider="mock", model="mock"),
        speech=SpeechConfig(enabled=True),
        speech_input=SpeechInputConfig(enabled=True),
        vision=VisionConfig(),
        extras={},
    )
    registry = create_builtin_registry()
    dispatcher = ActionDispatcher(ActionExecutor(registry=registry, mini=FakeMini()))
    brain = BrainProcessor(
        BrainAgent(
            config=config,
            registry=registry,
            run_action=dispatcher.run_action,
            client_factory=lambda options: OfflineSDKClient(
                options,
                action_runner=dispatcher.run_action,
            ),
        )
    )
    speech = SpeechPresenter()
    tts = KokoroAdapter(config.speech)

    start = time.monotonic()
    sdk_frames = await brain.process(
        TranscriptionFrame(text="你好", is_final=True, turn_id="T1", lang="zh")
    )
    reply_at = time.monotonic()
    sdk_message = sdk_frames[0]
    speech_frames = await speech.process(sdk_message)
    tts_frames = []
    for speech_frame in speech_frames:
        tts_frames.extend(await tts.process(speech_frame))
    result = await dispatcher.run_action(
        ActionSpec(
            name="nod",
            params={"cycles": 1, "period_s": 0.3},
            request_id="smoke_action",
            owner_id="main-agent",
        )
    )
    result_frame = ActionResultFrame.from_result(result)
    done_at = time.monotonic()

    assert isinstance(sdk_message, SDKMessageFrame)
    assert sdk_message.turn_id == "T1"
    assert type(sdk_message.message).__name__ == "AssistantMessage"
    assert all(isinstance(frame, SpeechPresenterFrame) for frame in speech_frames)
    assert speech_frames[-1].is_final is True
    assert all(isinstance(frame, TTSAudioFrame) for frame in tts_frames)
    assert tts_frames[-1].is_final is True
    assert isinstance(result_frame, ActionResultFrame)
    assert result_frame.owner_id == "main-agent"
    assert result_frame.status == "ok"
    assert (reply_at - start) <= 0.6
    assert (done_at - start) <= 2.0
