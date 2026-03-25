"""Tests for the front-only runtime pieces."""

import asyncio
from pathlib import Path

from langchain_core.messages import AIMessage

from reachy_mini.agent_runtime.front_service import FrontService
from reachy_mini.agent_runtime.memory import MemoryView
from reachy_mini.agent_runtime.profile_loader import load_profile_workspace


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


def test_front_service_uses_profile_workspace_context(tmp_path: Path) -> None:
    """Front replies should be built from the new profile workspace files."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_workspace(profile_root)
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
        assert "## Agent 规则" in model.messages[1].content
        assert "不要撒谎" in model.messages[1].content
        assert "用户喜欢直接一点" in model.messages[1].content
        assert "温柔、稳定" in model.messages[1].content
        assert "不要假装已经运行工具" in model.messages[1].content
        assert "昨天也卡住了" in model.messages[1].content
        assert "帮我看看日志" in model.messages[1].content

    asyncio.run(_exercise())
