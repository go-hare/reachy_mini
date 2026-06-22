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
from reachy_mini.action_runtime import ActionSpec  # noqa: E402
from reachy_mini.action_runtime.library import create_builtin_registry  # noqa: E402
from reachy_mini.pipeline import session as session_module  # noqa: E402
from reachy_mini.pipeline.action_dispatcher import ActionDispatcher  # noqa: E402
from reachy_mini.pipeline.frames import (  # noqa: E402
    ActionResultFrame,
    EmbodimentFrame,
    PipelineErrorFrame,
    SDKMessageFrame,
    SpeechActivityFrame,
    TTSStopFrame,
    VisionEventFrame,
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


def _write_live2d_profile(tmp_path: Path) -> Path:
    profile_root = tmp_path / "demo_app" / "profiles"
    static_root = tmp_path / "demo_app" / "demo_app" / "static"
    model_root = static_root / "assets" / "live2d" / "IceGirl"
    model_root.mkdir(parents=True)
    profile_root.mkdir(parents=True)
    (profile_root / "config.jsonl").write_text(
        '{"kind":"model","provider":"mock","model":"mock"}\n',
        encoding="utf-8",
    )
    (static_root / "avatar.config.json").write_text(
        (
            '{"mode":"live2d","live2d":{'
            '"root":"/static/assets/live2d/IceGirl",'
            '"vtube":"IceGirl.vtube.json"}}'
        ),
        encoding="utf-8",
    )
    (model_root / "IceGirl.vtube.json").write_text(
        (
            '{"FileReferences":{'
            '"Model":"IceGirl.model3.json",'
            '"IdleAnimation":"DaiJi.motion3.json"},'
            '"Hotkeys":['
            '{"Action":"TriggerAnimation","File":"HuiShou.motion3.json"},'
            '{"Action":"ToggleExpression","File":"惊讶.exp3.json"}]}'
        ),
        encoding="utf-8",
    )
    (model_root / "IceGirl.model3.json").write_text(
        (
            '{"FileReferences":{'
            '"Moc":"IceGirl.moc3",'
            '"DisplayInfo":"IceGirl.cdi3.json"}}'
        ),
        encoding="utf-8",
    )
    (model_root / "IceGirl.cdi3.json").write_text(
        (
            '{"Version":3,"Parameters":['
            '{"Id":"Param58","Name":"挥手"},'
            '{"Id":"Param59","Name":"挥手"},'
            '{"Id":"JingYa","Name":"惊讶"}]}'
        ),
        encoding="utf-8",
    )
    (model_root / "DaiJi.motion3.json").write_text(
        '{"Meta":{"Duration":12},"Curves":[]}',
        encoding="utf-8",
    )
    (model_root / "HuiShou.motion3.json").write_text(
        (
            '{"Meta":{"Duration":7},"Curves":['
            '{"Target":"Parameter","Id":"Param58"},'
            '{"Target":"Parameter","Id":"Param59"}]}'
        ),
        encoding="utf-8",
    )
    (model_root / "惊讶.exp3.json").write_text(
        '{"Parameters":[{"Id":"JingYa"}]}',
        encoding="utf-8",
    )
    return profile_root


class _HangingSDKClient(ScriptedSDKClient):
    """SDK fake that never yields a terminal response."""

    async def receive_response(self):
        """Sleep until the session timeout cancels this response stream."""
        await asyncio.sleep(10)
        if False:
            yield result_message()


class _Live2DToolThenHangingSDKClient(ScriptedSDKClient):
    """SDK fake that runs Live2D tools but never sends final assistant text."""

    async def receive_response(self):
        if self.action_runner is not None:
            await self.action_runner(
                ActionSpec(
                    name="live2d_motion_huishou",
                    request_id="live2d_motion_probe",
                    owner_id="main-agent",
                )
            )
            await self.action_runner(
                ActionSpec(
                    name="live2d_expression_jing_ya",
                    request_id="live2d_expression_probe",
                    owner_id="main-agent",
                )
            )
        await asyncio.sleep(10)
        if False:
            yield result_message()


def test_from_profile_live2d_mode_replaces_builtin_action_registry(tmp_path: Path) -> None:
    """Live2D mode registers only native Live2D actions for the Brain."""
    profile_root = _write_live2d_profile(tmp_path)

    session = RuntimeSession.from_profile(
        profile_root,
        client_factory=lambda options: OfflineSDKClient(options),
    )

    names = {metadata.name for metadata in session.registry.list_metadata()}
    assert names == {
        "live2d_motion_daiji",
        "live2d_motion_huishou",
        "live2d_expression_jing_ya",
    }
    assert "DaiJi" in session.agent.system_prompt_append
    assert "HuiShou" in session.agent.system_prompt_append
    assert "惊讶" in session.agent.system_prompt_append
    assert session.agent.options.allowed_tools == [
        "live2d_motion_daiji",
        "mcp__reachy_actions__live2d_motion_daiji",
        "live2d_motion_huishou",
        "mcp__reachy_actions__live2d_motion_huishou",
        "live2d_expression_jing_ya",
        "mcp__reachy_actions__live2d_expression_jing_ya",
    ]


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
async def test_vision_event_is_published_to_output_bus() -> None:
    """Browser surfaces should receive vision events without waiting for Brain output."""
    session = _build_session()
    await session.start()
    sub = session.subscribe()

    frame = VisionEventFrame(
        event="attention_acquired",
        payload={"direction": "front"},
        ts_ms=123,
    )
    await session.submit_vision_event(frame)

    published = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
    assert published == frame

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
async def test_surface_phase_publishes_embodiment_events() -> None:
    """Surface changes also emit body-agnostic events for avatars and 3D bodies."""
    session = _build_session(
        scripts=[
            [assistant_message("ok", session_id="T1"), result_message(session_id="T1")]
        ]
    )
    await session.start()
    sub = session.subscribe(filter=lambda frame: isinstance(frame, EmbodimentFrame))

    turn_id = await session.submit_text("hi", turn_id="T1")
    await session.wait_for_turn_idle(turn_id, timeout=2.0)

    frames = await _drain(sub)
    surface_events = [
        frame for frame in frames if frame.action == "surface_state"
    ]
    phases = [frame.payload["phase"] for frame in surface_events]
    assert "replying" in phases
    assert phases[-1] == "idle"
    replying = next(frame for frame in surface_events if frame.payload["phase"] == "replying")
    assert replying.payload["pose"] == "speak"
    assert replying.payload["attention"] == "front"

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
async def test_live2d_tool_success_reports_brain_timeout_when_text_times_out(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Live2D tool events must not synthesize assistant text on SDK timeout."""
    monkeypatch.setattr(session_module, "BRAIN_TURN_TIMEOUT_S", 0.01)
    monkeypatch.setattr(session_module, "BRAIN_STOP_TIMEOUT_S", 0.5)
    profile_root = _write_live2d_profile(tmp_path)
    created: dict[str, _Live2DToolThenHangingSDKClient] = {}

    def client_factory(options: Any) -> _Live2DToolThenHangingSDKClient:
        client = _Live2DToolThenHangingSDKClient(
            options,
            action_runner=session.actions.run_action,
        )
        created["client"] = client
        return client

    session = RuntimeSession.from_profile(profile_root, client_factory=client_factory)
    await session.start()
    sub = session.subscribe()

    await session.submit_text("你好", turn_id="T_live2d")

    frames = await _drain(sub)
    sdk_frames = [frame for frame in frames if isinstance(frame, SDKMessageFrame)]
    errors = [frame for frame in frames if isinstance(frame, PipelineErrorFrame)]
    action_results = [frame for frame in frames if isinstance(frame, ActionResultFrame)]
    embodiment = [frame for frame in frames if isinstance(frame, EmbodimentFrame)]

    assert [frame.name for frame in action_results] == [
        "live2d_motion_huishou",
        "live2d_expression_jing_ya",
    ]
    assert [frame.action for frame in embodiment if frame.target == "live2d"] == [
        "live2d_motion",
        "live2d_expression",
    ]
    assert not sdk_frames
    assert errors
    assert errors[-1].component == "brain"
    assert "timed out" in errors[-1].reason
    assert errors[-1].metadata["turn_id"] == "T_live2d"
    assert created["client"].connected is False
    assert session.agent._client is None

    await session.stop()


@pytest.mark.asyncio
async def test_live2d_plain_promise_does_not_trigger_runtime_action(
    tmp_path: Path,
) -> None:
    """Plain Live2D promises must not become keyword-triggered runtime actions."""
    profile_root = _write_live2d_profile(tmp_path)
    session = RuntimeSession.from_profile(
        profile_root,
        client_factory=lambda options: ScriptedSDKClient(
            options,
            scripts=[
                [
                    assistant_message("我来挥手。", session_id="T_live2d_text_only"),
                    result_message(session_id="T_live2d_text_only"),
                ]
            ],
        ),
    )
    await session.start()
    sub = session.subscribe()

    await session.submit_text("举手", turn_id="T_live2d_text_only")

    frames = await _drain(sub)
    action_results = [frame for frame in frames if isinstance(frame, ActionResultFrame)]

    assert not [frame for frame in action_results if frame.name.startswith("live2d_")]

    await session.stop()


@pytest.mark.asyncio
async def test_live2d_claimed_native_action_does_not_run_runtime_action(
    tmp_path: Path,
) -> None:
    """Assistant text claims must not drive Live2D without an actual tool call."""
    profile_root = _write_live2d_profile(tmp_path)
    session = RuntimeSession.from_profile(
        profile_root,
        client_factory=lambda options: ScriptedSDKClient(
            options,
            scripts=[
                [
                    assistant_message(
                        "已执行「举手」动作，对应的原生动作为 **HuiShou（挥手）**。",
                        session_id="T_live2d_claim",
                    ),
                    result_message(session_id="T_live2d_claim"),
                ]
            ],
        ),
    )
    await session.start()
    sub = session.subscribe()

    await session.submit_text("举手", turn_id="T_live2d_claim")

    frames = await _drain(sub)
    action_results = [frame for frame in frames if isinstance(frame, ActionResultFrame)]

    assert not [frame for frame in action_results if frame.name.startswith("live2d_")]

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
