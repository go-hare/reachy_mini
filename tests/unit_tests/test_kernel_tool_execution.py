"""Tests for real workspace tool execution inside the brain kernel."""

import asyncio
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from reachy_mini.core import BrainKernel, JsonlMemoryStore
from reachy_mini.runtime.profile_loader import load_profile_bundle
from reachy_mini.runtime.scheduler import _build_kernel_system_prompt
from reachy_mini.runtime.tool_loader import build_runtime_tool_bundle


class SimpleTaskRouterModel:
    """Always classify the turn as a simple task."""

    async def ainvoke(self, messages: list[Any]) -> dict[str, str]:
        _ = messages
        return {"task_type": "simple"}


class FileWritingToolModel:
    """Issue one write_file tool call, then finish with a direct reply."""

    def __init__(self) -> None:
        self.bound_tools: list[dict[str, Any]] = []
        self.call_count = 0

    def bind_tools(self, tools: list[dict[str, Any]]):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        _ = messages
        self.call_count += 1
        if self.call_count == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "toolcall_write_add",
                        "name": "write_file",
                        "args": {
                            "file_path": "add.py",
                            "content": "def add(a, b):\n    return a + b\n",
                        },
                    }
                ],
            )
        return AIMessage(content="已经创建 add.py，里面的 add(a, b) 会返回 a + b。")


def _write_profile(profile_root: Path) -> None:
    for filename, content in {
        "AGENTS.md": "保持真实",
        "USER.md": "用户偏好中文",
        "SOUL.md": "温柔、可靠",
        "TOOLS.md": "如果涉及文件改动，必须通过真实工具执行，不要假装已经完成。",
        "FRONT.md": "表达自然。",
        "config.jsonl": (
            '{"kind":"front","mode":"text","style":"friendly_concise","history_limit":4}\n'
            '{"kind":"front_model","provider":"mock","model":"reachy_mini_front_mock","temperature":0.4}\n'
            '{"kind":"kernel_model","provider":"mock","model":"reachy_mini_kernel_mock","temperature":0.2}\n'
        ),
    }.items():
        (profile_root / filename).write_text(content, encoding="utf-8")
    for directory in ("memory", "skills", "session", "tools", "prompts"):
        (profile_root / directory).mkdir()


def test_brain_kernel_executes_workspace_write_file_tool(tmp_path: Path) -> None:
    """A write_file tool call should create the file on disk."""

    async def _exercise() -> None:
        app_root = tmp_path / "demo_app"
        profile_root = app_root / "profiles"
        profile_root.mkdir(parents=True)
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        tool_bundle = build_runtime_tool_bundle(profile)
        kernel_model = FileWritingToolModel()
        kernel = BrainKernel(
            agent_id=profile.name,
            model=kernel_model,
            task_router_model=SimpleTaskRouterModel(),
            tools=tool_bundle.all_tools,
            memory_store=JsonlMemoryStore(profile.root),
            system_prompt=_build_kernel_system_prompt(
                profile,
                workspace_root=tool_bundle.workspace_root,
                system_tool_names=tool_bundle.system_tool_names,
                profile_tool_names=tool_bundle.profile_tool_names,
            ),
        )

        response = await kernel.handle_user_input(
            conversation_id="cli:main",
            user_id="user",
            turn_id="turn_1",
            text="创建一个 add.py，定义 add(a, b) 返回 a + b。",
            latest_front_reply="我先帮你处理这个文件改动。",
        )

        created = app_root / "add.py"
        assert created.exists()
        assert created.read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
        assert response.reply == "已经创建 add.py，里面的 add(a, b) 会返回 a + b。"
        assert kernel_model.bound_tools

    asyncio.run(_exercise())
