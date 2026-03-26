"""Tests for the resident-kernel agent runner."""

import asyncio
from pathlib import Path

from reachy_mini.runtime.config import load_profile_runtime_config
from reachy_mini.runtime.profile_loader import load_profile_bundle
from reachy_mini.runtime.scheduler import RuntimeScheduler


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


async def _collect_final_reply(runtime: RuntimeScheduler, *, thread_id: str, user_text: str) -> str:
    queue = runtime.subscribe_front_outputs()
    final_reply = ""
    try:
        await runtime.handle_user_text(
            thread_id=thread_id,
            session_id=thread_id,
            user_id="user",
            user_text=user_text,
        )
        await runtime.wait_for_thread_idle(thread_id)
        while not queue.empty():
                packet = queue.get_nowait()
                try:
                    if packet.thread_id == thread_id and packet.type == "front_final_done":
                        final_reply = str(packet.text or "").strip()
                finally:
                    queue.task_done()
        return final_reply
    finally:
        runtime.unsubscribe_front_outputs(queue)


def test_front_agent_runner_persists_dialogue(tmp_path: Path) -> None:
    """The resident runner should persist both front and brain records."""

    async def _exercise() -> None:
        profile_root = tmp_path / "demo"
        profile_root.mkdir()
        _write_profile(profile_root)

        profile = load_profile_bundle(profile_root)
        config = load_profile_runtime_config(profile)
        runtime = RuntimeScheduler.from_profile(
            profile=profile,
            config=config,
            enable_affect=False,
        )
        await runtime.start()
        try:
            reply = await _collect_final_reply(
                runtime,
                thread_id="cli:main",
                user_text="帮我看看日志",
            )
        finally:
            await runtime.stop()

        front_path = runtime.kernel.memory_store.path_for_conversation_stream(
            "cli:main",
            "front.jsonl",
        )
        brain_path = runtime.kernel.memory_store.path_for_conversation_stream(
            "cli:main",
            "brain.jsonl",
        )
        front_text = front_path.read_text(encoding="utf-8")
        brain_text = brain_path.read_text(encoding="utf-8")

        assert reply == "需要先查看和“帮我看看日志”相关的文件或日志，确认后才能给你准确结论。"
        assert '"front_reply": "我先帮你看一下帮我看看日志，看完马上回来跟你说。"' in front_text
        assert '"role": "user"' in brain_text
        assert '"role": "assistant"' in brain_text

    asyncio.run(_exercise())
