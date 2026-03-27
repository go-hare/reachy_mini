"""Tests for the front runtime pieces."""

import asyncio
from pathlib import Path

from langchain_core.messages import AIMessage

from reachy_mini.core.memory import MemoryView
from reachy_mini.front import FrontSignal
from reachy_mini.runtime.profile_loader import load_profile_bundle
from reachy_mini.front import FrontService


class RecordingModel:
    """Small async test double for the front service."""

    def __init__(self) -> None:
        """Start with no captured messages."""
        self.messages = None

    async def ainvoke(self, messages):
        """Capture the prompt and return a canned reply."""
        self.messages = messages
        return AIMessage(content="前台回复")


def _write_profile(profile_root: Path) -> None:
    for filename, content in {
        "AGENTS.md": "不要撒谎",
        "USER.md": "用户喜欢直接一点",
        "SOUL.md": "温柔、稳定",
        "TOOLS.md": "不要假装已经运行工具",
        "FRONT.md": "你要像贴在身边的陪伴者一样说话。",
        "config.jsonl": '{"kind":"front","mode":"text","style":"friendly_concise"}\n',
    }.items():
        (profile_root / filename).write_text(content, encoding="utf-8")
    for directory in ("memory", "skills", "session", "tools", "prompts"):
        (profile_root / directory).mkdir()


def test_front_service_uses_profile_bundle_context(tmp_path: Path) -> None:
    """Front replies should be built from the app profile files."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        model = RecordingModel()
        service = FrontService(profile, model)

        reply = await service.reply(
            user_text="帮我看看日志",
            memory=MemoryView(
                raw_layer={
                    "recent_dialogue": [
                        {"role": "user", "content": "昨天也卡住了"},
                        {"role": "assistant", "content": "我记得，我们继续查。"},
                    ]
                },
                projections={
                    "agent_anchor": profile.agents_md,
                    "user_anchor": profile.user_md,
                    "soul_anchor": profile.soul_md,
                    "tool_anchor": profile.tools_md,
                },
            ),
            style="friendly_concise",
        )

        assert reply == "前台回复"
        assert model.messages is not None
        assert "你要像贴在身边的陪伴者一样说话。" in model.messages[0].content
        assert "## 外显目标" in model.messages[1].content
        assert "用户喜欢直接一点" in model.messages[1].content
        assert "温柔、稳定" in model.messages[1].content
        assert "这是一个需要核实事实的请求" in model.messages[1].content
        assert "帮我看看日志" in model.messages[1].content
        assert "昨天也卡住了" not in model.messages[1].content

    asyncio.run(_exercise())


def test_front_service_accepts_expressive_signals(tmp_path: Path) -> None:
    """Front should expose a formal signal entrypoint for lifecycle events."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        model = RecordingModel()
        service = FrontService(profile, model)

        result = await service.handle_signal(
            FrontSignal(
                name="kernel_output_ready",
                thread_id="cli:main",
                turn_id="turn-1",
                user_text="帮我看看日志",
                metadata={"kernel_output": "需要先查看日志", "motion_hint": "nod"},
            )
        )

        history = service.get_signal_history("cli:main")
        latest = service.get_latest_signal_result("cli:main")

        assert result.lifecycle_state == "replying"
        assert result.surface_patch["phase"] == "replying"
        assert result.surface_patch["source_signal"] == "kernel_output_ready"
        assert result.surface_patch["motion_hint"] == "nod"
        assert result.surface_patch["presence"] == "near"
        assert result.surface_patch["body_state"] == "leaning_in"
        assert result.surface_patch["has_kernel_output"] is True
        assert result.reply_text == ""
        assert result.tool_calls == []
        assert len(history) == 1
        assert history[0].name == "kernel_output_ready"
        assert latest == result

    asyncio.run(_exercise())


def test_front_service_decides_idle_tool_call_from_signal(tmp_path: Path) -> None:
    """Idle lifecycle should yield a front-owned do_nothing intention when available."""

    class FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        model = RecordingModel()
        service = FrontService(profile, model, tools=[FakeTool("do_nothing")])

        result = await service.handle_signal(
            FrontSignal(
                name="idle_entered",
                thread_id="cli:main",
                turn_id="turn-2",
            )
        )

        assert result.lifecycle_state == "idle"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "do_nothing"
        assert result.tool_calls[0].arguments["reason"]
        assert "do_nothing" in result.debug_reason

    asyncio.run(_exercise())


def test_front_service_decides_move_head_from_vision_signal(tmp_path: Path) -> None:
    """Vision attention signals should be convertible into explicit front tool calls."""

    class FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        model = RecordingModel()
        service = FrontService(profile, model, tools=[FakeTool("move_head")])

        result = await service.handle_signal(
            FrontSignal(
                name="vision_attention_updated",
                thread_id="cli:main",
                turn_id="turn-3",
                metadata={"direction": "left"},
            )
        )

        assert result.lifecycle_state == "attending"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "move_head"
        assert result.tool_calls[0].arguments == {"direction": "left"}
        assert "move_head:left" in result.debug_reason

    asyncio.run(_exercise())


def test_front_service_yields_floor_when_user_speech_starts(tmp_path: Path) -> None:
    """User speech should clear front-owned expressive loops before listening."""

    class FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        model = RecordingModel()
        service = FrontService(
            profile,
            model,
            tools=[FakeTool("stop_emotion"), FakeTool("stop_dance")],
        )

        result = await service.handle_signal(
            FrontSignal(
                name="user_speech_started",
                thread_id="cli:main",
                turn_id="turn-4",
            )
        )

        assert result.lifecycle_state == "listening"
        assert result.surface_patch["presence"] == "beside"
        assert [call.tool_name for call in result.tool_calls] == ["stop_emotion", "stop_dance"]
        assert "user speech" in result.debug_reason

    asyncio.run(_exercise())


def test_front_service_holds_listening_wait_and_settling_postures(tmp_path: Path) -> None:
    """Listening-wait and settling should expose stable hold semantics to the runtime."""

    class FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        model = RecordingModel()
        service = FrontService(profile, model, tools=[FakeTool("do_nothing")])

        listening_wait = await service.handle_signal(
            FrontSignal(
                name="user_speech_stopped",
                thread_id="cli:main",
                turn_id="turn-5",
            )
        )
        settling = await service.handle_signal(
            FrontSignal(
                name="settling_entered",
                thread_id="cli:main",
                turn_id="turn-5",
            )
        )

        assert listening_wait.lifecycle_state == "listening_wait"
        assert listening_wait.surface_patch["presence"] == "steady"
        assert listening_wait.surface_patch["body_state"] == "steady_listening"
        assert listening_wait.surface_patch["recommended_hold_ms"] == 600
        assert listening_wait.tool_calls[0].tool_name == "do_nothing"

        assert settling.lifecycle_state == "settling"
        assert settling.surface_patch["motion_hint"] == "stay_close"
        assert settling.surface_patch["body_state"] == "resting_close"
        assert settling.surface_patch["recommended_hold_ms"] == 900
        assert settling.tool_calls[0].tool_name == "do_nothing"

    asyncio.run(_exercise())


def test_front_service_decides_idle_tick_lookaround_when_move_head_is_available(
    tmp_path: Path,
) -> None:
    """Idle ticks should trigger a lightweight look-around instead of a static hold."""

    class FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        model = RecordingModel()
        service = FrontService(profile, model, tools=[FakeTool("move_head")])

        first = await service.handle_signal(
            FrontSignal(
                name="idle_tick",
                thread_id="cli:main",
                turn_id="turn-4",
            )
        )
        second = await service.handle_signal(
            FrontSignal(
                name="idle_tick",
                thread_id="cli:main",
                turn_id="turn-4",
            )
        )

        assert first.lifecycle_state == "idle"
        assert first.tool_calls[0].tool_name == "move_head"
        assert first.tool_calls[0].arguments == {"direction": "left"}
        assert second.tool_calls[0].arguments == {"direction": "right"}
        assert "idle_tick" in second.debug_reason

    asyncio.run(_exercise())
