"""Integration tests for the single-brain RuntimeScheduler."""

from __future__ import annotations

import asyncio
import copy
import threading
from pathlib import Path

from reachy_mini.runtime.config import load_profile_runtime_config
from reachy_mini.runtime.profile_loader import load_profile_bundle
from reachy_mini.runtime.scheduler import RuntimeScheduler


def _write_profile(profile_root: Path) -> None:
    for filename, content in {
        "AGENTS.md": "保持真实",
        "USER.md": "用户偏好中文",
        "SOUL.md": "温柔、可靠",
        "TOOLS.md": "需要查看文件或日志时再动工具。",
        "FRONT.md": "直接、自然、简洁回复。",
        "config.jsonl": (
            '{"kind":"front","mode":"text","style":"friendly_concise","history_limit":4}\n'
            '{"kind":"brain_model","provider":"mock","model":"reachy_runtime_mock","temperature":0.2}\n'
        ),
    }.items():
        (profile_root / filename).write_text(content, encoding="utf-8")
    for directory in ("memory", "skills", "session", "tools", "prompts"):
        (profile_root / directory).mkdir()


def test_runtime_scheduler_collects_single_brain_reply(tmp_path: Path) -> None:
    """The runtime should surface a clean final reply from the single-brain mock."""

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

        queue = None
        await runtime.start()
        try:
            queue = runtime.subscribe_front_outputs()
            await runtime.handle_user_turn(
                thread_id="cli:main",
                session_id="cli:main",
                user_id="user",
                user_text="帮我看看日志",
            )
            await runtime.wait_for_thread_idle("cli:main")

            final_reply = ""
            while not queue.empty():
                packet = queue.get_nowait()
                try:
                    if packet.thread_id == "cli:main" and packet.type == "turn_done":
                        final_reply = str(packet.text or "").strip()
                finally:
                    queue.task_done()
        finally:
            if queue is not None:
                runtime.unsubscribe_front_outputs(queue)
            await runtime.stop()

        assert "帮我看看日志" in final_reply
        assert final_reply.endswith("相关的文件或日志，确认后才能给你准确结论。")

    asyncio.run(_exercise())


def test_runtime_scheduler_updates_speech_surface_states(tmp_path: Path) -> None:
    """Speech lifecycle hooks should update listening and waiting surface states."""

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

        observed_states: list[dict[str, str]] = []

        async def surface_state_handler(state: dict[str, str]) -> None:
            observed_states.append(dict(state))

        await runtime.start()
        try:
            await runtime.handle_user_speech_started(
                thread_id="cli:main",
                user_text="你好",
                surface_state_handler=surface_state_handler,
            )
            await runtime.handle_user_speech_partial(
                thread_id="cli:main",
                user_text="你好",
                surface_state_handler=surface_state_handler,
            )
            await runtime.handle_user_speech_stopped(
                thread_id="cli:main",
                user_text="你好",
                surface_state_handler=surface_state_handler,
            )
        finally:
            await runtime.stop()

        assert observed_states[0]["phase"] == "listening"
        assert observed_states[-1]["phase"] == "listening_wait"
        assert runtime.get_thread_surface_state("cli:main") is None

    asyncio.run(_exercise())


def test_runtime_scheduler_keeps_handlers_out_of_message_metadata(tmp_path: Path) -> None:
    """Runtime callbacks should stay outside persisted agent message metadata."""

    class ReplyAudioHandler:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.payloads: list[dict[str, str]] = []

        async def handle(self, payload: dict[str, str]) -> bool:
            with self._lock:
                self.payloads.append(dict(payload))
            return True

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

        reply_audio = ReplyAudioHandler()

        await runtime.start()
        try:
            await runtime.handle_user_turn(
                thread_id="cli:main",
                session_id="cli:main",
                user_id="user",
                user_text="帮我看看日志",
                final_reply_handler=reply_audio.handle,
            )
            await runtime.wait_for_thread_idle("cli:main")

            copied_messages = copy.deepcopy(list(runtime.agent._messages))  # type: ignore[attr-defined]
        finally:
            await runtime.stop()

        assert copied_messages
        assert reply_audio.payloads
        last_user_message = next(
            message for message in reversed(copied_messages) if message.role == "user"
        )
        assert "final_reply_handler" not in last_user_message.metadata
        assert "surface_state_handler" not in last_user_message.metadata

    asyncio.run(_exercise())


def test_runtime_scheduler_uses_profile_scoped_memory_roots(tmp_path: Path) -> None:
    """The resident runtime should persist memory/session data under the app profile."""

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

        store = runtime.agent._memory_adapter.store  # type: ignore[attr-defined]
        session_store = runtime.agent._session_store  # type: ignore[attr-defined]

        assert store.profile_root == profile_root.resolve()
        assert store.memory_root == (profile_root / "memory").resolve()
        assert store.session_root == (profile_root / "session").resolve()
        assert session_store is not None
        assert session_store.session_dir == (profile_root / "sessions").resolve()

    asyncio.run(_exercise())
