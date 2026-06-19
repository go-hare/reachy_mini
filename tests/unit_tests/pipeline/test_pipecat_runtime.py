"""Tests for the real Pipecat-backed L2 runtime shell."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameProcessor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdk_fakes import (  # noqa: E402
    FakeMini,
    ScriptedSDKClient,
    agent_config,
    assistant_message,
    result_message,
)

from reachy_mini.action_runtime import ActionExecutor  # noqa: E402
from reachy_mini.action_runtime.library import create_builtin_registry  # noqa: E402
from reachy_mini.pipeline.action_dispatcher import ActionDispatcher  # noqa: E402
from reachy_mini.pipeline.frames import SDKMessageFrame  # noqa: E402
from reachy_mini.pipeline.pipecat_runtime import (  # noqa: E402
    ReachyBrainPipecatProcessor,
    ReachyPipecatRuntime,
    ReachyPipelineFrame,
)
from reachy_mini.pipeline.session import RuntimeSession  # noqa: E402
from reachy_mini.pipeline.speech_presenter import SpeechPresenter  # noqa: E402
from reachy_mini.pipeline.tts_kokoro import KokoroAdapter  # noqa: E402
from reachy_mini.reachy_brain.agent import BrainAgent  # noqa: E402


def _session() -> RuntimeSession:
    config = agent_config()
    registry = create_builtin_registry()
    executor = ActionExecutor(registry=registry, mini=FakeMini())
    actions = ActionDispatcher(executor)

    def client_factory(options: Any) -> ScriptedSDKClient:
        return ScriptedSDKClient(
            options,
            scripts=[[assistant_message("ok", session_id="T1"), result_message(session_id="T1")]],
            action_runner=actions.run_action,
        )

    agent = BrainAgent(
        config=config,
        registry=registry,
        run_action=actions.run_action,
        client_factory=client_factory,
    )
    return RuntimeSession(
        config=config,
        registry=registry,
        agent=agent,
        executor=executor,
        speech=SpeechPresenter(),
        tts=KokoroAdapter(config.speech),
        actions=actions,
    )


def test_l2_runtime_uses_real_pipecat_types() -> None:
    """The L2 runtime shell is backed by Pipecat Pipeline and PipelineWorker."""
    session = _session()
    runtime = ReachyPipecatRuntime(
        bus=session.bus,
        brain=session.brain,
        speech=session.speech,
        tts=session.tts,
        actions=session.actions,
        on_interrupt=session._handle_interrupt,
        on_sdk_message=session._handle_sdk_message_side_effects,
        on_brain_timeout=session._handle_brain_timeout,
        on_brain_error=session._handle_brain_exception,
        brain_turn_timeout_s=1.0,
        audio_in_sample_rate=16000,
        audio_out_sample_rate=24000,
    )

    assert isinstance(runtime.pipeline, Pipeline)
    assert isinstance(runtime.worker, PipelineWorker)
    assert isinstance(runtime.input, FrameProcessor)
    assert any(
        isinstance(processor, ReachyBrainPipecatProcessor)
        for processor in runtime.pipeline.processors
    )


@pytest.mark.asyncio
async def test_runtime_session_text_turn_flows_through_pipecat() -> None:
    """A RuntimeSession text turn uses the Pipecat runtime shell."""
    session = _session()
    await session.start()
    assert isinstance(session.pipecat, ReachyPipecatRuntime)

    sub = session.subscribe(filter=lambda frame: isinstance(frame, SDKMessageFrame))
    await session.submit_text("hi", turn_id="T1")

    frames: list[SDKMessageFrame] = []
    while not sub.queue.empty():
        frames.append(await sub.queue.get())

    assert frames
    assert all(isinstance(frame, SDKMessageFrame) for frame in frames)
    assert isinstance(ReachyPipelineFrame(payload=frames[0]), ReachyPipelineFrame)

    await session.stop()
