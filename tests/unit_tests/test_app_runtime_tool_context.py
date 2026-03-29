"""Tests for passing runtime tool context into resident runtimes."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
import time
from unittest.mock import patch

import numpy as np

from reachy_mini import ReachyMiniApp
from reachy_mini.runtime.tools import ReachyToolContext


def _write_profile(profile_root: Path, *, config_jsonl: str | None = None) -> None:
    for filename, content in {
        "AGENTS.md": "保持真实",
        "USER.md": "用户偏好中文",
        "SOUL.md": "温柔、可靠",
        "TOOLS.md": "如果有真实工具就要真实执行。",
        "FRONT.md": "表达自然。",
        "config.jsonl": config_jsonl
        or (
            '{"kind":"front","mode":"text","style":"friendly_concise","history_limit":4}\n'
            '{"kind":"vision","no_camera":false,"head_tracker":"","local_vision":false}\n'
            '{"kind":"front_model","provider":"mock","model":"reachy_mini_front_mock","temperature":0.4}\n'
            '{"kind":"kernel_model","provider":"mock","model":"reachy_mini_kernel_mock","temperature":0.2}\n'
        ),
    }.items():
        (profile_root / filename).write_text(content, encoding="utf-8")
    for directory in ("memory", "skills", "session", "tools", "prompts"):
        (profile_root / directory).mkdir()


def _make_fake_robot(*, with_media: bool = False) -> object:
    attributes = {
        "goto_target": lambda self, **kwargs: None,
        "set_target": lambda self, **kwargs: None,
        "get_current_head_pose": lambda self: np.eye(4, dtype=np.float64),
        "get_current_joint_positions": lambda self: ([0.0] * 7, [0.0, 0.0]),
    }
    if with_media:
        attributes["media"] = SimpleNamespace(get_frame=lambda: None)
    return type("FakeRobot", (), attributes)()


class FakeMovementManager:
    """Simple movement-manager stub for app-context tests."""

    def __init__(self, robot: object, camera_worker: object = None) -> None:
        self.robot = robot
        self.camera_worker = camera_worker
        self.started = False
        self.stopped = False
        self.speech_offsets: list[tuple[float, float, float, float, float, float]] = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def set_speech_offsets(
        self,
        offsets: tuple[float, float, float, float, float, float],
    ) -> None:
        self.speech_offsets.append(offsets)


class FakeHeadWobbler:
    """Simple head-wobbler stub for app-context tests."""

    def __init__(self, set_speech_offsets):
        self.set_speech_offsets = set_speech_offsets
        self.started = False
        self.stopped = False
        self.fed: list[str] = []
        self.reset_called = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def feed(self, delta_b64: str) -> None:
        self.fed.append(delta_b64)

    def reset(self) -> None:
        self.reset_called = True


class ToolContextApp(ReachyMiniApp):
    """Minimal app subclass that uses the default resident-runtime builder."""

    def __init__(self, profile_root: Path) -> None:
        self.profile_root_relative_path = str(profile_root)
        super().__init__()


def test_build_runtime_passes_runtime_tool_context(tmp_path: Path) -> None:
    """ReachyMiniApp.build_runtime should forward the prepared tool context."""
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    _write_profile(profile_root)

    app = ToolContextApp(profile_root)
    app.runtime_tool_context = ReachyToolContext(reachy_mini=object())

    with patch(
        "reachy_mini.runtime.scheduler.RuntimeScheduler.from_profile",
        return_value="runtime",
    ) as runtime_factory:
        runtime = app.build_runtime(profile_root)

    assert runtime == "runtime"
    assert runtime_factory.call_args.kwargs["runtime_tool_context"] is app.runtime_tool_context


def test_build_runtime_tool_context_creates_movement_manager(tmp_path: Path) -> None:
    """Resident apps should prepare a movement manager for Reachy tools."""
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    _write_profile(profile_root)

    app = ToolContextApp(profile_root)
    fake_robot = _make_fake_robot()

    with patch("reachy_mini.runtime.moves.MovementManager", FakeMovementManager), patch(
        "reachy_mini.runtime.audio.HeadWobbler",
        FakeHeadWobbler,
    ):
        context = app.build_runtime_tool_context(fake_robot)

    assert context is not None
    assert context.reachy_mini is fake_robot
    assert context.movement_manager is not None
    assert context.movement_manager.started is True
    assert context.head_wobbler is not None
    assert context.head_wobbler.started is True
    assert context.speech_driver is not None
    assert context.surface_driver is not None
    assert context.embodiment_coordinator is not None
    assert context.embodiment_coordinator.speech_driver is context.speech_driver
    assert context.surface_driver.movement_manager is context.movement_manager


def test_build_runtime_tool_context_starts_camera_worker_when_media_is_available(
    tmp_path: Path,
) -> None:
    """Resident apps should start a camera worker when the robot exposes media frames."""
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    _write_profile(profile_root)

    app = ToolContextApp(profile_root)
    fake_robot = _make_fake_robot(with_media=True)

    class FakeWorker:
        def __init__(self, robot: object, head_tracker: object = None):
            self.robot = robot
            self.head_tracker = head_tracker
            self.started = False
            self.stopped = False

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

    with patch("reachy_mini.runtime.camera_worker.CameraWorker", FakeWorker), patch(
        "reachy_mini.runtime.moves.MovementManager",
        FakeMovementManager,
    ), patch(
        "reachy_mini.runtime.audio.HeadWobbler",
        FakeHeadWobbler,
    ):
        context = app.build_runtime_tool_context(fake_robot)

    assert context is not None
    assert context.camera_worker is not None
    assert context.camera_worker.started is True
    assert context.movement_manager is not None
    assert context.movement_manager.camera_worker is context.camera_worker
    assert context.head_wobbler is not None
    assert context.head_wobbler.started is True
    assert context.speech_driver is not None
    assert context.surface_driver is not None
    assert context.embodiment_coordinator is not None

    app.cleanup_runtime_tool_context(context)
    assert context.head_wobbler.stopped is True
    assert context.movement_manager.stopped is True
    assert context.camera_worker.stopped is True


def test_runtime_audio_helpers_delegate_to_head_wobbler(tmp_path: Path) -> None:
    """ReachyMiniApp should expose a small public hook for audio delta feeding."""
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    _write_profile(profile_root)

    app = ToolContextApp(profile_root)
    fake_robot = _make_fake_robot()

    with patch("reachy_mini.runtime.moves.MovementManager", FakeMovementManager), patch(
        "reachy_mini.runtime.audio.HeadWobbler",
        FakeHeadWobbler,
    ):
        context = app.build_runtime_tool_context(fake_robot)

    assert context is not None
    app.runtime_tool_context = context
    assert app.feed_runtime_audio_delta("demo-audio") is True
    assert context.head_wobbler.fed == ["demo-audio"]
    assert app.reset_runtime_audio_motion() is True
    assert context.head_wobbler.reset_called is True


def test_build_runtime_tool_context_honors_no_camera_setting(tmp_path: Path) -> None:
    """Legacy no_camera=true should skip camera worker startup."""
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    _write_profile(
        profile_root,
        config_jsonl=(
            '{"kind":"vision","no_camera":true,"head_tracker":"yolo","local_vision":true}\n'
        ),
    )

    app = ToolContextApp(profile_root)
    fake_robot = _make_fake_robot(with_media=True)

    with patch("reachy_mini.runtime.moves.MovementManager", FakeMovementManager), patch(
        "reachy_mini.runtime.audio.HeadWobbler",
        FakeHeadWobbler,
    ):
        context = app.build_runtime_tool_context(fake_robot)

    assert context is not None
    assert context.camera_worker is None
    assert context.vision_processor is None
    assert context.movement_manager is not None
    assert context.head_wobbler is not None
    assert context.speech_driver is not None
    assert context.surface_driver is not None
    assert context.embodiment_coordinator is not None


def test_runtime_embodiment_helpers_delegate_to_coordinator(tmp_path: Path) -> None:
    """Public app hooks should prefer the coordinator once Stage 4 wiring exists."""
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    _write_profile(profile_root)

    app = ToolContextApp(profile_root)

    class FakeCoordinator:
        def __init__(self) -> None:
            self.surface_states: list[dict[str, str]] = []
            self.audio_deltas: list[str] = []
            self.reset_calls = 0

        def apply_surface_state(self, state: dict[str, str]) -> None:
            self.surface_states.append(dict(state))

        def feed_audio_delta(self, delta_b64: str) -> bool:
            self.audio_deltas.append(delta_b64)
            return True

        def reset_speech_motion(self) -> bool:
            self.reset_calls += 1
            return True

    coordinator = FakeCoordinator()
    app.runtime_tool_context = ReachyToolContext(
        reachy_mini=object(),
        embodiment_coordinator=coordinator,
    )

    app.apply_runtime_surface_state({"thread_id": "app:test", "phase": "replying"})
    assert app.feed_runtime_audio_delta("demo-audio") is True
    assert app.reset_runtime_audio_motion() is True

    assert coordinator.surface_states == [{"thread_id": "app:test", "phase": "replying"}]
    assert coordinator.audio_deltas == ["demo-audio"]
    assert coordinator.reset_calls == 1


def test_runtime_reply_audio_helper_delegates_to_reply_audio_service(tmp_path: Path) -> None:
    """ReachyMiniApp should expose a small public hook for final reply audio playback."""
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    _write_profile(profile_root)

    app = ToolContextApp(profile_root)

    class FakeReplyAudioService:
        def __init__(self) -> None:
            self.texts: list[str] = []

        async def speak_text(self, text: str) -> bool:
            self.texts.append(text)
            return True

    service = FakeReplyAudioService()
    app.runtime_tool_context = ReachyToolContext(
        reachy_mini=object(),
        reply_audio_service=service,
    )

    result = asyncio.run(
        app.play_runtime_reply_audio({"thread_id": "app:test", "turn_id": "turn-1", "text": "final reply"})
    )

    assert result is True
    assert service.texts == ["final reply"]


def test_runtime_reply_audio_helper_passes_lifecycle_callbacks_when_supported(
    tmp_path: Path,
) -> None:
    """ReachyMiniApp should forward audio lifecycle callbacks to callback-aware services."""

    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    _write_profile(profile_root)

    app = ToolContextApp(profile_root)
    callback_events: list[str] = []

    class FakeReplyAudioService:
        async def speak_text(
            self,
            text: str,
            *,
            on_started=None,
            on_audio_delta=None,
            on_finished=None,
        ) -> bool:
            callback_events.append(text)
            if on_started is not None:
                await on_started()
            if on_audio_delta is not None:
                await on_audio_delta("demo-delta")
            if on_finished is not None:
                await on_finished(True)
            return True

    async def _on_started() -> None:
        callback_events.append("started")

    async def _on_audio_delta(delta_b64: str) -> None:
        callback_events.append(delta_b64)

    async def _on_finished(played_any: bool) -> None:
        callback_events.append(f"finished:{played_any}")

    app.runtime_tool_context = ReachyToolContext(
        reachy_mini=object(),
        reply_audio_service=FakeReplyAudioService(),
    )

    result = asyncio.run(
        app.play_runtime_reply_audio(
            {
                "thread_id": "app:test",
                "turn_id": "turn-1",
                "text": "final reply",
                "on_started": _on_started,
                "on_audio_delta": _on_audio_delta,
                "on_finished": _on_finished,
            }
        )
    )

    assert result is True
    assert callback_events == [
        "final reply",
        "started",
        "demo-delta",
        "finished:True",
    ]


def test_runtime_microphone_bridge_blocks_input_during_reply_audio_cooldown(
    tmp_path: Path,
) -> None:
    """Resident microphone bridge should stay blocked briefly after reply audio playback."""

    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    _write_profile(
        profile_root,
        config_jsonl=(
            '{"kind":"front","mode":"text","style":"friendly_concise","history_limit":4}\n'
            '{"kind":"speech_input","enabled":true,"provider":"mlx_whisper","model":"mlx-community/whisper-small-mlx","playback_block_cooldown_ms":900}\n'
            '{"kind":"front_model","provider":"mock","model":"reachy_mini_front_mock","temperature":0.4}\n'
            '{"kind":"kernel_model","provider":"mock","model":"reachy_mini_kernel_mock","temperature":0.2}\n'
        ),
    )

    app = ToolContextApp(profile_root)
    app.runtime_config = SimpleNamespace(
        speech_input=SimpleNamespace(
            enabled=True,
            provider="mlx_whisper",
            model="mlx-community/whisper-small-mlx",
            language="zh",
            playback_block_cooldown_ms=900,
        ),
        front_model=SimpleNamespace(api_key="", base_url=""),
    )

    class FakeBridge:
        def __init__(self, **kwargs) -> None:
            self.input_blocked = kwargs["input_blocked"]

    captured: dict[str, object] = {}

    def _build_bridge(**kwargs):
        bridge = FakeBridge(**kwargs)
        captured["bridge"] = bridge
        return bridge

    async def _fake_play_runtime_reply_audio(context, payload):
        _ = context
        _ = payload
        await asyncio.sleep(0)
        return True

    app.runtime_tool_context = SimpleNamespace(
        reachy_mini=SimpleNamespace(media=object()),
        speech_driver=SimpleNamespace(speech_active=False),
        reply_audio_service=SimpleNamespace(_active_playback_task=None),
    )

    with patch(
        "reachy_mini.runtime.speech_input.build_runtime_speech_input_transcriber",
        return_value=object(),
    ), patch(
        "reachy_mini.runtime.speech_input.RuntimeMicrophoneBridge",
        side_effect=_build_bridge,
    ):
        bridge = app._build_runtime_microphone_bridge(runtime=SimpleNamespace())

    assert bridge is captured["bridge"]
    assert bridge.input_blocked() is False

    with patch.object(
        app.runtime_host_adapter,
        "play_runtime_reply_audio",
        side_effect=_fake_play_runtime_reply_audio,
    ):
        result = asyncio.run(
            app.play_runtime_reply_audio(
                {"thread_id": "app:test", "turn_id": "turn-1", "text": "final reply"}
            )
        )

    assert result is True
    assert bridge.input_blocked() is True

    app._runtime_speech_input_block_until = time.monotonic() - 1.0
    assert bridge.input_blocked() is False


def test_build_runtime_tool_context_builds_reply_audio_service_when_enabled(
    tmp_path: Path,
) -> None:
    """Resident apps should prepare optional reply audio service from speech config."""
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    _write_profile(
        profile_root,
        config_jsonl=(
            '{"kind":"front","mode":"text","style":"friendly_concise","history_limit":4}\n'
            '{"kind":"vision","no_camera":false,"head_tracker":"","local_vision":false}\n'
            '{"kind":"front_model","provider":"mock","model":"reachy_mini_front_mock","temperature":0.4,"api_key":"front-secret"}\n'
            '{"kind":"kernel_model","provider":"mock","model":"reachy_mini_kernel_mock","temperature":0.2}\n'
            '{"kind":"speech","enabled":true,"provider":"openai","model":"gpt-4o-mini-tts","voice":"alloy"}\n'
        ),
    )

    app = ToolContextApp(profile_root)
    fake_robot = _make_fake_robot()
    fake_robot.media = SimpleNamespace(
        start_playing=lambda: None,
        push_audio_sample=lambda data: None,
        stop_playing=lambda: None,
        get_output_audio_samplerate=lambda: 24_000,
    )
    sentinel_service = object()

    with patch("reachy_mini.runtime.moves.MovementManager", FakeMovementManager), patch(
        "reachy_mini.runtime.audio.HeadWobbler",
        FakeHeadWobbler,
    ), patch(
        "reachy_mini.runtime.reply_audio.build_runtime_reply_audio_service",
        return_value=sentinel_service,
    ) as builder:
        context = app.build_runtime_tool_context(fake_robot)

    assert context is not None
    assert context.reply_audio_service is sentinel_service
    assert builder.call_args.kwargs["fallback_api_key"] == "front-secret"


def test_build_runtime_tool_context_builds_yolo_tracker_and_local_vision(
    tmp_path: Path,
) -> None:
    """Configured legacy vision options should be started from config.jsonl."""
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    _write_profile(
        profile_root,
        config_jsonl=(
            '{"kind":"vision","no_camera":false,"head_tracker":"yolo","local_vision":true,"local_vision_model":"demo-model","hf_home":"./demo-cache"}\n'
        ),
    )

    app = ToolContextApp(profile_root)
    fake_robot = _make_fake_robot(with_media=True)

    class FakeWorker:
        def __init__(self, robot: object, head_tracker: object):
            self.robot = robot
            self.head_tracker = head_tracker
            self.started = False

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            pass

    with patch("reachy_mini.runtime.camera_worker.CameraWorker", FakeWorker), patch(
        "reachy_mini.runtime.moves.MovementManager",
        FakeMovementManager,
    ), patch(
        "reachy_mini.runtime.audio.HeadWobbler",
        FakeHeadWobbler,
    ), patch(
        "reachy_mini.runtime.vision.yolo_head_tracker.HeadTracker",
        return_value="yolo-tracker",
    ) as head_tracker_factory, patch(
        "reachy_mini.runtime.vision.processors.initialize_vision_processor",
        return_value="local-vision",
    ) as vision_factory:
        context = app.build_runtime_tool_context(fake_robot)

    assert context is not None
    assert context.camera_worker is not None
    assert context.camera_worker.started is True
    assert context.camera_worker.head_tracker == "yolo-tracker"
    assert context.vision_processor == "local-vision"
    assert context.movement_manager is not None
    assert context.head_wobbler is not None
    head_tracker_factory.assert_called_once_with()
    vision_kwargs = vision_factory.call_args.args[0]
    assert vision_kwargs.model_path == "demo-model"
    assert vision_kwargs.hf_home == "./demo-cache"
