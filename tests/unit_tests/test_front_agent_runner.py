"""Smoke tests for the single-brain resident runtime."""

from __future__ import annotations

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
        "TOOLS.md": "按需使用工具",
        "FRONT.md": "直接、自然、简洁回复。",
        "config.jsonl": (
            '{"kind":"front","mode":"text","style":"friendly_concise","history_limit":4}\n'
            '{"kind":"brain_model","provider":"mock","model":"reachy_runtime_mock","temperature":0.2}\n'
        ),
    }.items():
        (profile_root / filename).write_text(content, encoding="utf-8")
    for directory in ("memory", "skills", "session", "tools", "prompts"):
        (profile_root / directory).mkdir()


async def _collect_turn_packets(
    runtime: RuntimeScheduler,
    *,
    thread_id: str,
    user_text: str,
) -> list[tuple[str, str]]:
    queue = runtime.subscribe_front_outputs()
    packets: list[tuple[str, str]] = []
    try:
        await runtime.handle_user_turn(
            thread_id=thread_id,
            session_id=thread_id,
            user_id="user",
            user_text=user_text,
        )
        await runtime.wait_for_thread_idle(thread_id)
        while not queue.empty():
            packet = queue.get_nowait()
            try:
                if packet.thread_id == thread_id:
                    packets.append((packet.type, str(packet.text or "")))
            finally:
                queue.task_done()
        return packets
    finally:
        runtime.unsubscribe_front_outputs(queue)


def test_single_brain_runtime_emits_turn_done(tmp_path: Path) -> None:
    """The resident runtime should emit single-brain packets for one turn."""

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
            packets = await _collect_turn_packets(
                runtime,
                thread_id="cli:main",
                user_text="你好",
            )
        finally:
            await runtime.stop()

        packet_types = [packet_type for packet_type, _ in packets]
        assert "speech_preview" in packet_types
        assert "thinking" in packet_types
        assert "text_delta" in packet_types
        assert "turn_done" in packet_types
        assert packets[-1] == ("turn_done", "收到，我会围绕“你好”继续处理。")

    asyncio.run(_exercise())
