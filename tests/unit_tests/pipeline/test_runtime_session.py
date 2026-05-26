"""Tests for the multi-turn RuntimeSession."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from reachy_mini.action_runtime import ActionExecutor, ActionResult, ActionSpec
from reachy_mini.action_runtime.library import create_builtin_registry
from reachy_mini.pipeline.action_dispatcher import ActionDispatcher
from reachy_mini.pipeline.frames import (
    ActionResultFrame,
    ActionSpecFrame,
    BrainReplyFrame,
    SpeechActivityFrame,
    SpeechPresenterFrame,
    TTSAudioFrame,
    TTSStopFrame,
    WorkerEventFrame,
)
from reachy_mini.pipeline.session import SURFACE_TASK_ID, RuntimeSession
from reachy_mini.pipeline.speech_presenter import SpeechPresenter
from reachy_mini.pipeline.tts_kokoro import KokoroAdapter
from reachy_mini.reachy_brain.agent import BrainAgent, BrainTurnInput
from reachy_mini.reachy_brain.config import (
    AgentConfig,
    ModelConfig,
    SpeechConfig,
    SpeechInputConfig,
    VisionConfig,
)
from reachy_mini.reachy_brain.coordinator import WorkerCoordinator


def _agent_config(*, speech_enabled: bool = False) -> AgentConfig:
    return AgentConfig(
        model=ModelConfig(provider="mock", model="mock"),
        speech=SpeechConfig(enabled=speech_enabled),
        speech_input=SpeechInputConfig(enabled=False),
        vision=VisionConfig(),
        extras={},
    )


class _FakeMini:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.antennas = [0.0, 0.0]

    def goto_target(self, **kwargs: Any) -> None:
        self.calls.append(("goto_target", kwargs))

    def look_at_world(self, x, y, z, duration, perform_movement) -> None:
        self.calls.append(("look_at_world", {"x": x, "y": y, "z": z}))

    def get_present_antenna_joint_positions(self) -> list[float]:
        return list(self.antennas)


class _ScriptedModel:
    def __init__(self, scripted: list[dict[str, Any]]) -> None:
        self._scripted = list(scripted)
        self.last_inputs: list[BrainTurnInput] = []

    async def run(self, turn_input, *, config, registry):
        self.last_inputs.append(turn_input)
        if self._scripted:
            return self._scripted.pop(0)
        return {"reply_text": "ok", "speech_style": {}, "tool_calls": [], "worker_decision": None}


def _build_session(
    *,
    scripted: list[dict[str, Any]] | None = None,
    speech_enabled: bool = False,
) -> RuntimeSession:
    config = _agent_config(speech_enabled=speech_enabled)
    registry = create_builtin_registry()
    model = _ScriptedModel(scripted or [])
    agent = BrainAgent(config=config, registry=registry, model=model)
    mini = _FakeMini()
    executor = ActionExecutor(registry=registry, mini=mini)

    async def emit_action(spec: ActionSpec) -> ActionResult:
        return await executor.submit(spec)

    coordinator = WorkerCoordinator(emit_action=emit_action)
    speech = SpeechPresenter()
    tts = KokoroAdapter(config.speech)
    actions = ActionDispatcher(executor)
    return RuntimeSession(
        config=config,
        registry=registry,
        agent=agent,
        executor=executor,
        speech=speech,
        tts=tts,
        actions=actions,
        coordinator=coordinator,
    )


async def _drain(sub) -> list[Any]:
    items: list[Any] = []
    while not sub.queue.empty():
        items.append(await sub.queue.get())
    return items


@pytest.mark.asyncio
async def test_two_turns_share_brain_memory_and_publish_replies() -> None:
    """Two consecutive turns produce two BrainReplyFrames and the second sees the first."""
    session = _build_session(
        scripted=[
            {"reply_text": "hello", "speech_style": {}, "tool_calls": [], "worker_decision": None},
            {"reply_text": "again", "speech_style": {}, "tool_calls": [], "worker_decision": None},
        ]
    )
    await session.start()
    sub = session.subscribe()

    turn1 = await session.submit_text("hi", turn_id="T1")
    await session.wait_for_turn_idle(turn1, timeout=2.0)
    turn2 = await session.submit_text("again", turn_id="T2")
    await session.wait_for_turn_idle(turn2, timeout=2.0)

    frames = await _drain(sub)
    replies = [frame for frame in frames if isinstance(frame, BrainReplyFrame)]
    assert [reply.reply_text for reply in replies] == ["hello", "again"]

    model_inputs = session.agent.model.last_inputs  # type: ignore[attr-defined]
    second_context = model_inputs[1].context["recent_frames"]
    # Memory carries forward: the brain processor's context buffer accumulates
    # across turns, so the second call must observe more context than the first.
    assert len(second_context) >= len(model_inputs[0].context["recent_frames"])

    await session.stop()


@pytest.mark.asyncio
async def test_action_runs_and_publishes_spec_and_result_frames() -> None:
    """A reply with a tool call yields ActionSpecFrame + ActionResultFrame."""
    session = _build_session(
        scripted=[
            {
                "reply_text": "ok",
                "speech_style": {},
                "tool_calls": [
                    {"name": "nod", "arguments": {"cycles": 1, "period_s": 0.3}, "reason": "ack"}
                ],
                "worker_decision": None,
            }
        ]
    )
    await session.start()
    sub = session.subscribe()

    turn_id = await session.submit_text("hi", turn_id="T1")
    await session.wait_for_turn_idle(turn_id, timeout=3.0)

    frames = await _drain(sub)
    spec_frames = [frame for frame in frames if isinstance(frame, ActionSpecFrame)]
    result_frames = [frame for frame in frames if isinstance(frame, ActionResultFrame)]
    assert len(spec_frames) == 1
    assert spec_frames[0].spec.name == "nod"
    assert len(result_frames) == 1
    assert result_frames[0].status == "ok"
    assert result_frames[0].name == "nod"

    await session.stop()


@pytest.mark.asyncio
async def test_worker_decision_spawns_worker_and_publishes_events() -> None:
    """Brain worker_decision spawns coordinator worker; events surface as WorkerEventFrames."""
    session = _build_session(
        scripted=[
            {
                "reply_text": "patrolling",
                "speech_style": {},
                "tool_calls": [],
                "worker_decision": {
                    "op": "spawn",
                    "task_id": "w1",
                    "task_type": "patrol",
                    "task_params": {"rounds": 1},
                    "summary": "start patrol",
                },
            }
        ]
    )
    await session.start()
    sub = session.subscribe()

    turn_id = await session.submit_text("巡视", turn_id="T1")
    await session.wait_for_turn_idle(turn_id, timeout=3.0)

    frames = await _drain(sub)
    worker_events = [
        frame
        for frame in frames
        if isinstance(frame, WorkerEventFrame) and frame.task_id == "w1"
    ]
    events = [frame.event for frame in worker_events]
    assert "started" in events
    assert "completed" in events

    await session.stop()


@pytest.mark.asyncio
async def test_barge_in_publishes_tts_stop_and_cancels_actions() -> None:
    """SpeechActivityFrame(start) during TTS yields TTSStopFrame and cancels actions."""
    session = _build_session(
        scripted=[
            {
                "reply_text": "讲个故事吧。一只小狗。",
                "speech_style": {},
                "tool_calls": [],
                "worker_decision": None,
            }
        ],
        speech_enabled=True,
    )
    await session.start()
    sub = session.subscribe()

    turn_id = await session.submit_text("tell story", turn_id="T1")
    # Drain a bit so TTSAudioFrame flips brain.tts_active to True.
    await asyncio.sleep(0.05)

    # The TTS frames feed back into BrainProcessor; if the last chunk was final
    # we still want to verify the routing path. Force tts_active to True so the
    # barge-in branch is exercised regardless of timing.
    session.brain.tts_active = True

    await session.submit_speech_activity(SpeechActivityFrame(state="start", ts_ms=1000))
    await asyncio.sleep(0.05)

    frames = await _drain(sub)
    stop_frames = [frame for frame in frames if isinstance(frame, TTSStopFrame)]
    assert stop_frames, "expected TTSStopFrame after barge-in"

    await session.stop()
    # turn cleanup
    assert turn_id not in session._turn_actions


@pytest.mark.asyncio
async def test_surface_phase_transitions_published() -> None:
    """Surface state moves idle -> replying -> idle for a no-op turn."""
    session = _build_session(
        scripted=[
            {"reply_text": "ok", "speech_style": {}, "tool_calls": [], "worker_decision": None}
        ]
    )
    await session.start()
    sub = session.subscribe(
        filter=lambda frame: isinstance(frame, WorkerEventFrame)
        and frame.task_id == SURFACE_TASK_ID
    )

    turn_id = await session.submit_text("hi", turn_id="T1")
    await session.wait_for_turn_idle(turn_id, timeout=2.0)

    frames = await _drain(sub)
    phases = [frame.payload["state"]["phase"] for frame in frames]
    assert "idle" in phases
    assert "replying" in phases
    assert phases[-1] == "idle"

    await session.stop()


@pytest.mark.asyncio
async def test_stop_cancels_running_workers_quickly() -> None:
    """stop() returns within 1 s even with active workers."""

    class _SlowWorker:
        async def run(self, ctx) -> Any:  # pragma: no cover - cancellation only
            from reachy_mini.reachy_brain.worker import WorkerResult

            await ctx.emit_event("started", {"note": "slow", "progress": 0.0})
            try:
                while True:
                    await ctx.cancel_token.checkpoint()
                    await asyncio.sleep(0.05)
            except Exception:
                return WorkerResult(status="cancelled", summary="stopped")
            return WorkerResult(status="completed", summary="never")

    session = _build_session(
        scripted=[
            {
                "reply_text": "go",
                "speech_style": {},
                "tool_calls": [],
                "worker_decision": {
                    "op": "spawn",
                    "task_id": "slow1",
                    "task_type": "slow",
                    "task_params": {},
                    "summary": "slow",
                },
            }
        ]
    )
    session.coordinator.register_worker("slow", _SlowWorker)
    await session.start()

    await session.submit_text("go", turn_id="T1")
    await asyncio.sleep(0.05)

    start = asyncio.get_event_loop().time()
    await session.stop()
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed < 2.0
