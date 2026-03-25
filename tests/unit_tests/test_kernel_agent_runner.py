"""Tests for the real brain-kernel-backed runtime runner."""

import asyncio
from pathlib import Path

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
        runner = FrontAgentRunner.from_profile(profile=profile, config=config)

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
