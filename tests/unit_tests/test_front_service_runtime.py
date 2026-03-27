"""Tests for the front runtime pieces."""

import asyncio
from pathlib import Path

from langchain_core.messages import AIMessage

from reachy_mini.affect import AffectState, EmotionSignal, PADVector
from reachy_mini.companion import CompanionIntent, SurfaceExpression
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
        assert "## task" in model.messages[1].content
        assert "## verification_mode" in model.messages[1].content
        assert "用户喜欢直接一点" in model.messages[1].content
        assert "温柔、稳定" in model.messages[1].content
        assert "- requires_verification: true" in model.messages[1].content
        assert "帮我看看日志" in model.messages[1].content
        assert "昨天也卡住了" not in model.messages[1].content
        assert "## 外显目标" not in model.messages[1].content

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
                metadata={"kernel_output": "需要先查看日志"},
            )
        )

        history = service.get_signal_history("cli:main")
        latest = service.get_latest_signal_result("cli:main")

        assert result.lifecycle_state == "replying"
        assert result.surface_patch["phase"] == "replying"
        assert result.surface_patch["source_signal"] == "kernel_output_ready"
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
        assert result.surface_patch["phase"] == "listening"
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
        assert listening_wait.surface_patch["recommended_hold_ms"] == 600
        assert listening_wait.tool_calls[0].tool_name == "do_nothing"

        assert settling.lifecycle_state == "settling"
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


def test_front_service_presentation_prompt_uses_thin_structured_context(tmp_path: Path) -> None:
    """Presentation prompts should carry raw context, not local prose guidance tables."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        model = RecordingModel()
        service = FrontService(profile, model)

        reply = await service.present(
            user_text="我有点烦，帮我看看日志",
            kernel_output="先看 app.log，再检查连接状态。",
            affect_state=AffectState(
                current_pad=PADVector(pleasure=-0.3, arousal=0.4, dominance=-0.1),
                vitality=0.45,
                pressure=0.62,
            ),
            emotion_signal=EmotionSignal(
                primary_emotion="frustrated",
                intensity=0.72,
                confidence=0.81,
                support_need="focused",
                wants_action=True,
                trigger_text="帮我看看日志",
            ),
            companion_intent=CompanionIntent(
                mode="focused",
                warmth=0.82,
                initiative=0.58,
                intensity=0.49,
            ),
            surface_expression=SurfaceExpression(
                text_style="warm_clear",
                expression="attentive_warm",
            ),
        )

        assert reply == "前台回复"
        assert model.messages is not None
        prompt = model.messages[1].content
        assert "## affect_state" in prompt
        assert "## emotion_signal" in prompt
        assert "## companion_intent" in prompt
        assert "## surface_expression" in prompt
        assert "- mode: focused" in prompt
        assert "- text_style: warm_clear" in prompt
        assert "- expression: attentive_warm" in prompt
        assert "- wants_action: true" in prompt
        assert "## 陪伴节奏建议" not in prompt
        assert "## 语言手感" not in prompt
        assert "## 情绪动力学" not in prompt
        assert "## 语义情绪" not in prompt
        assert "## 外显风格" not in prompt

    asyncio.run(_exercise())


def test_front_service_reply_prompt_keeps_memory_blocks_for_non_verification(tmp_path: Path) -> None:
    """Non-verification replies should still receive thin memory context blocks."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        model = RecordingModel()
        service = FrontService(profile, model)

        reply = await service.reply(
            user_text="谢谢你，继续吧",
            memory=MemoryView(
                raw_layer={
                    "recent_dialogue": [
                        {"role": "user", "content": "刚刚那段解释挺清楚"},
                        {"role": "assistant", "content": "那我们接着往前。"},
                    ],
                    "recent_tools": [
                        {"tool_name": "task_status", "content": "日志读取完成"},
                    ],
                },
                cognitive_layer=[
                    {"summary": "用户更喜欢短句直接一点", "outcome": "keep"},
                ],
                long_term_layer={"summary": "用户通常希望先给结论再展开。"},
                projections={
                    "user_anchor": profile.user_md,
                    "soul_anchor": profile.soul_md,
                },
            ),
            emotion_signal=EmotionSignal(
                primary_emotion="happy",
                intensity=0.35,
                confidence=0.76,
                support_need="encourage",
                wants_action=False,
                trigger_text="谢谢你",
            ),
        )

        assert reply == "前台回复"
        assert model.messages is not None
        prompt = model.messages[1].content
        assert "## emotion_signal" in prompt
        assert "- primary_emotion: happy" in prompt
        assert "## recent_dialogue" in prompt
        assert "刚刚那段解释挺清楚" in prompt
        assert "## recent_tools" in prompt
        assert "日志读取完成" in prompt
        assert "## cognitive_summary" in prompt
        assert "## long_term_summary" in prompt
        assert "## verification_mode" not in prompt
        assert "## 关系信号" not in prompt

    asyncio.run(_exercise())
