"""Tests for the SDK-first RuntimeSession."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdk_fakes import (  # noqa: E402
    FakeMini,
    ScriptedSDKClient,
    agent_config,
    assistant_message,
    result_message,
    task_completed,
    task_progress,
    task_started,
)

from reachy_mini.action_runtime import ActionExecutor  # noqa: E402
from reachy_mini.action_runtime.library import create_builtin_registry  # noqa: E402
from reachy_mini.pipeline import session as session_module  # noqa: E402
from reachy_mini.pipeline.action_dispatcher import ActionDispatcher  # noqa: E402
from reachy_mini.pipeline.frames import (  # noqa: E402
    ActionResultFrame,
    PipelineErrorFrame,
    SDKMessageFrame,
    SpeechActivityFrame,
    TTSStopFrame,
    WorkerEventFrame,
)
from reachy_mini.pipeline.session import SURFACE_TASK_ID, RuntimeSession  # noqa: E402
from reachy_mini.pipeline.speech_presenter import SpeechPresenter  # noqa: E402
from reachy_mini.pipeline.tts_kokoro import KokoroAdapter  # noqa: E402
from reachy_mini.reachy_brain.agent import BrainAgent  # noqa: E402
from reachy_mini.reachy_brain.offline_sdk_client import OfflineSDKClient  # noqa: E402


def _build_session(
    *,
    scripts: list[list[Any]] | None = None,
    offline: bool = False,
    speech_enabled: bool = False,
    client_factory_override: Any | None = None,
) -> RuntimeSession:
    config = agent_config(speech_enabled=speech_enabled)
    registry = create_builtin_registry()
    executor = ActionExecutor(registry=registry, mini=FakeMini())
    published: list[ActionResultFrame] = []

    async def publish_result(frame: ActionResultFrame) -> None:
        published.append(frame)

    actions = ActionDispatcher(executor, publish=publish_result)

    def client_factory(options: Any) -> Any:
        if client_factory_override is not None:
            return client_factory_override(options, actions)
        if offline:
            return OfflineSDKClient(options, action_runner=actions.run_action)
        return ScriptedSDKClient(
            options,
            scripts=scripts or [],
            action_runner=actions.run_action,
        )

    agent = BrainAgent(
        config=config,
        registry=registry,
        run_action=actions.run_action,
        client_factory=client_factory,
    )
    session = RuntimeSession(
        config=config,
        registry=registry,
        agent=agent,
        executor=executor,
        speech=SpeechPresenter(),
        tts=KokoroAdapter(config.speech),
        actions=actions,
    )

    async def publish_to_session(frame: ActionResultFrame) -> None:
        await session.bus.publish(frame)

    actions._publish = publish_to_session  # test wiring mirrors from_profile()
    return session


async def _drain(sub) -> list[Any]:
    items: list[Any] = []
    while not sub.queue.empty():
        items.append(await sub.queue.get())
    return items


class _HangingSDKClient(ScriptedSDKClient):
    """SDK fake that never yields a terminal response."""

    async def receive_response(self):
        """Sleep until the session timeout cancels this response stream."""
        await asyncio.sleep(10)
        if False:
            yield result_message()


@pytest.mark.asyncio
async def test_two_turns_publish_sdk_messages_and_share_context() -> None:
    """Two turns produce SDKMessageFrames and the second sees accumulated context."""
    session = _build_session(
        scripts=[
            [
                assistant_message("hello", session_id="T1"),
                result_message(session_id="T1"),
            ],
            [
                assistant_message("again", session_id="T2"),
                result_message(session_id="T2"),
            ],
        ]
    )
    await session.start()
    sub = session.subscribe()

    turn1 = await session.submit_text("hi", turn_id="T1")
    await session.wait_for_turn_idle(turn1, timeout=2.0)
    turn2 = await session.submit_text("again", turn_id="T2")
    await session.wait_for_turn_idle(turn2, timeout=2.0)

    frames = await _drain(sub)
    sdk_frames = [frame for frame in frames if isinstance(frame, SDKMessageFrame)]
    assert [frame.turn_id for frame in sdk_frames[:2]] == ["T1", "T1"]
    assert any(
        type(frame.message).__name__ == "AssistantMessage" and frame.turn_id == "T2"
        for frame in sdk_frames
    )

    client = session.agent._client
    assert isinstance(client, ScriptedSDKClient)
    assert "recent_frames': []" in client.turn_inputs[0].context["formatted_prompt"]
    assert "SDKMessageFrame" in client.turn_inputs[1].context["formatted_prompt"]

    await session.stop()


@pytest.mark.asyncio
async def test_offline_sdk_action_tool_path_publishes_result_frame() -> None:
    """Explicit offline SDK fake can exercise MCP action execution."""
    session = _build_session(offline=True)
    await session.start()
    sub = session.subscribe()

    turn_id = await session.submit_text("hi", turn_id="T1")
    await session.wait_for_turn_idle(turn_id, timeout=3.0)

    frames = await _drain(sub)
    result_frames = [frame for frame in frames if isinstance(frame, ActionResultFrame)]
    assert len(result_frames) == 1
    assert result_frames[0].status == "ok"
    assert result_frames[0].name == "nod"

    await session.stop()


@pytest.mark.asyncio
async def test_sdk_task_messages_publish_worker_events() -> None:
    """SDK task messages surface as WorkerEventFrames without a Reachy-side worker."""
    session = _build_session(
        scripts=[
            [
                assistant_message("patrolling", session_id="T1"),
                task_started("w1", session_id="T1"),
                task_progress("w1", session_id="T1"),
                task_completed("w1", session_id="T1"),
                result_message(session_id="T1"),
            ]
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
    assert [frame.event for frame in worker_events] == [
        "started",
        "progress",
        "completed",
    ]

    await session.stop()


@pytest.mark.asyncio
async def test_barge_in_publishes_tts_stop_and_cancels_actions() -> None:
    """SpeechActivityFrame(start) during TTS yields TTSStopFrame and interrupts SDK."""
    session = _build_session(
        scripts=[
            [
                assistant_message("讲个故事吧。一只小狗。", session_id="T1"),
                result_message(session_id="T1"),
            ]
        ],
        speech_enabled=True,
    )
    await session.start()
    sub = session.subscribe()

    turn_id = await session.submit_text("tell story", turn_id="T1")
    await asyncio.sleep(0.05)
    session.brain.tts_active = True

    await session.submit_speech_activity(SpeechActivityFrame(state="start", ts_ms=1000))
    await asyncio.sleep(0.05)

    frames = await _drain(sub)
    stop_frames = [frame for frame in frames if isinstance(frame, TTSStopFrame)]
    assert stop_frames, "expected TTSStopFrame after barge-in"
    assert turn_id == "T1"

    client = session.agent._client
    assert isinstance(client, ScriptedSDKClient)
    assert client.interrupted is True

    await session.stop()


@pytest.mark.asyncio
async def test_surface_phase_transitions_published() -> None:
    """Surface state moves idle -> replying -> idle for a no-op turn."""
    session = _build_session(
        scripts=[
            [assistant_message("ok", session_id="T1"), result_message(session_id="T1")]
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
async def test_brain_timeout_publishes_error_and_resets_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stuck SDK turn should unblock the UI with a pipeline error."""
    monkeypatch.setattr(session_module, "BRAIN_TURN_TIMEOUT_S", 0.01)
    monkeypatch.setattr(session_module, "BRAIN_STOP_TIMEOUT_S", 0.5)
    created: dict[str, _HangingSDKClient] = {}

    def client_factory(options: Any, actions: ActionDispatcher) -> _HangingSDKClient:
        client = _HangingSDKClient(options, action_runner=actions.run_action)
        created["client"] = client
        return client

    session = _build_session(client_factory_override=client_factory)
    await session.start()
    sub = session.subscribe()

    await session.submit_text("hi", turn_id="T1")

    frames = await _drain(sub)
    errors = [frame for frame in frames if isinstance(frame, PipelineErrorFrame)]
    phases = [
        frame.payload["state"]["phase"]
        for frame in frames
        if isinstance(frame, WorkerEventFrame) and frame.task_id == SURFACE_TASK_ID
    ]
    assert errors
    assert errors[-1].component == "brain"
    assert "timed out" in errors[-1].reason
    assert errors[-1].metadata["turn_id"] == "T1"
    assert phases[-1] == "idle"
    assert created["client"].connected is False
    assert session.agent._client is None

    await session.stop()


@pytest.mark.asyncio
async def test_stop_disconnects_sdk_quickly() -> None:
    """stop() stops tracked SDK tasks and disconnects the SDK client."""
    session = _build_session(
        scripts=[
            [task_started("slow1", session_id="T1"), result_message(session_id="T1")]
        ]
    )
    await session.start()

    await session.submit_text("go", turn_id="T1")
    client = session.agent._client
    assert isinstance(client, ScriptedSDKClient)
    start = asyncio.get_event_loop().time()
    await session.stop()
    elapsed = asyncio.get_event_loop().time() - start

    assert elapsed < 2.0
    assert client.stopped_tasks == ["slow1"]
    assert client.connected is False
