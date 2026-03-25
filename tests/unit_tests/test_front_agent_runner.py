"""Tests for the front-only agent runner."""

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
        ),
    }.items():
        (profile_root / filename).write_text(content, encoding="utf-8")
    for directory in ("memory", "skills", "session", "tools", "prompts"):
        (profile_root / directory).mkdir()


def test_front_agent_runner_persists_dialogue(tmp_path: Path) -> None:
    """The minimal phase-2 runner should load a profile and emit text replies."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_workspace(profile_root)
        config = load_agent_profile_config(profile)
        runner = FrontAgentRunner(profile=profile, config=config)

        reply = await runner.reply(
            thread_id="cli:main",
            user_text="帮我看看日志",
        )

        session_path = runner.session_store.path_for_thread("cli:main")
        session_text = session_path.read_text(encoding="utf-8")

        assert "我先帮你看一下帮我看看日志" in reply
        assert '"role": "user"' in session_text
        assert '"role": "assistant"' in session_text

    asyncio.run(_exercise())
