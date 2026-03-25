"""Tests for the real brain-kernel-backed runtime runner."""

import asyncio
from pathlib import Path

from reachy_mini.affect import AffectState, AffectTurnResult, EmotionSignal, PADVector
from reachy_mini.agent_core import BrainResponse, TaskType
from reachy_mini.agent_core.memory import MemoryView
from reachy_mini.agent_runtime.config import load_agent_profile_config
from reachy_mini.agent_runtime.profile_loader import load_profile_workspace
from reachy_mini.agent_runtime.runner import FrontAgentRunner


def _write_profile(profile_root: Path) -> None:
    for filename, content in {
        "AGENTS.md": "保持真实",
        "USER.md": "用户偏好中文",
        "SOUL.md": "温柔、可靠",
        "TOOLS.md": "文本阶段先不要假装已经运行工具",
        "FRONT.md": "前台表达要自然、贴近、简洁。",
        "config.jsonl": (
            '{"kind":"front","mode":"text","style":"friendly_concise","history_limit":4}\n'
            '{"kind":"front_model","provider":"mock","model":"reachy_mini_front_mock","temperature":0.4}\n'
            '{"kind":"kernel_model","provider":"mock","model":"reachy_mini_kernel_mock","temperature":0.2}\n'
        ),
    }.items():
        (profile_root / filename).write_text(content, encoding="utf-8")
    for directory in ("memory", "skills", "session", "tools", "prompts"):
        (profile_root / directory).mkdir()


def test_kernel_agent_runner_uses_brain_kernel_records(tmp_path: Path) -> None:
    """The stage-3 runner should persist front and brain records via BrainKernel."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_workspace(profile_root)
        config = load_agent_profile_config(profile)
        runner = FrontAgentRunner.from_profile(
            profile=profile,
            config=config,
            enable_affect=False,
        )

        reply = await runner.reply(
            thread_id="cli:main",
            user_text="帮我看看日志",
        )

        front_path = profile_root / "session" / "cli_main" / "front.jsonl"
        brain_path = profile_root / "session" / "cli_main" / "brain.jsonl"
        front_text = front_path.read_text(encoding="utf-8")
        brain_text = brain_path.read_text(encoding="utf-8")

        assert reply == "需要先查看和“帮我看看日志”相关的文件或日志，确认后才能给你准确结论。"
        assert '"front_reply": "我先帮你看一下帮我看看日志，看完马上回来跟你说。"' in front_text
        assert '"role": "user"' in brain_text
        assert '"role": "assistant"' in brain_text

    asyncio.run(_exercise())


class FakeFront:
    """Test double for front reply/present calls."""

    def __init__(self) -> None:
        self.reply_calls: list[dict[str, object]] = []
        self.present_calls: list[dict[str, object]] = []

    async def reply(self, **kwargs):
        self.reply_calls.append(kwargs)
        return "front hint"

    async def present(self, **kwargs):
        self.present_calls.append(kwargs)
        return "front final"


class FakeMemoryStore:
    """Small memory-store stub for the runner."""

    def build_memory_view(
        self,
        conversation_id: str,
        agent_id: str,
        query: str,
        limit: int = 6,
    ) -> MemoryView:
        _ = conversation_id
        _ = agent_id
        _ = query
        _ = limit
        return MemoryView()


class FakeKernel:
    """Kernel stub that records front events and returns one response."""

    def __init__(self) -> None:
        self.agent_id = "demo"
        self.memory_store = FakeMemoryStore()
        self.front_events: list[dict[str, object]] = []
        self.user_inputs: list[dict[str, object]] = []

    async def handle_front_event(self, **kwargs):
        self.front_events.append(kwargs)
        return kwargs

    async def handle_user_input(self, **kwargs):
        self.user_inputs.append(kwargs)
        return BrainResponse(task_type=TaskType.simple, reply="kernel raw")


class FakeAffectRuntime:
    """Affect stub that returns a deterministic state and emotion."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.state = AffectState(
            current_pad=PADVector(pleasure=-0.3, arousal=0.4, dominance=0.1),
            vitality=0.62,
            pressure=0.48,
            updated_at="2026-03-26T10:00:00",
        )
        self.emotion_signal = EmotionSignal(
            primary_emotion="anxious",
            intensity=0.7,
            confidence=0.9,
            support_need="focused",
            wants_action=True,
            trigger_text="帮我看看日志",
        )

    def evolve(self, *, user_text: str) -> AffectTurnResult:
        self.calls.append(user_text)
        return AffectTurnResult(
            previous_state=self.state,
            state=self.state,
            user_pad=PADVector(),
            delta_pad=PADVector(),
            pressure_delta=0.0,
            emotion_signal=self.emotion_signal,
        )


def test_kernel_agent_runner_passes_affect_and_companion_through_front(tmp_path: Path) -> None:
    """Kernel mode should pass affect/emotion into front reply and present."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_workspace(profile_root)
        config = load_agent_profile_config(profile)
        front = FakeFront()
        kernel = FakeKernel()
        affect_runtime = FakeAffectRuntime()
        runner = FrontAgentRunner(
            profile=profile,
            config=config,
            front=front,
            kernel=kernel,
            affect_runtime=affect_runtime,
        )

        reply = await runner.reply(
            thread_id="cli:main",
            user_text="帮我看看日志",
        )

        assert reply == "front final"
        assert affect_runtime.calls == ["帮我看看日志"]
        assert front.reply_calls[0]["emotion_signal"] == affect_runtime.emotion_signal
        assert kernel.user_inputs[0]["latest_front_reply"] == "front hint"
        assert front.present_calls[0]["affect_state"] == affect_runtime.state
        assert front.present_calls[0]["emotion_signal"] == affect_runtime.emotion_signal
        assert front.present_calls[0]["companion_intent"] is not None
        assert front.present_calls[0]["surface_expression"] is not None
        assert kernel.front_events[0]["front_event"]["metadata"]["emotion_primary"] == "anxious"
        assert kernel.front_events[1]["front_event"]["metadata"]["kernel_output"] == "kernel raw"
        assert kernel.front_events[1]["front_event"]["metadata"]["mode"] == "focused"

    asyncio.run(_exercise())
