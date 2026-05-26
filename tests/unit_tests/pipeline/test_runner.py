"""Tests for the v4 pipeline runner and CLI hook."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from reachy_mini.pipeline.frames import ActionResultFrame, SDKMessageFrame
from reachy_mini.pipeline.runner import run_text_turn
from reachy_mini.pipeline.session import RuntimeSession
from reachy_mini.runtime.main import handle_agent
from reachy_mini.runtime.project import create_app_project


@dataclass
class TextBlock:
    text: str


@dataclass
class AssistantMessage:
    content: list[TextBlock]


class _FakeCLISession:
    """Minimal RuntimeSession stand-in for CLI wiring tests."""

    def __init__(self) -> None:
        self.subscriptions: list[tuple[Any, Any]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def subscribe(self, *, filter: Any = None) -> Any:
        sub = SimpleNamespace(queue=asyncio.Queue())
        self.subscriptions.append((sub, filter))
        return sub

    def unsubscribe(self, sub: Any) -> None:
        self.subscriptions = [item for item in self.subscriptions if item[0] is not sub]

    async def submit_text(self, text: str, *, turn_id: str | None = None) -> str:
        actual_turn = turn_id or "generated-thread"
        frame = SDKMessageFrame(
            message=AssistantMessage(content=[TextBlock(text=f"我听到了：{text}")]),
            turn_id=actual_turn,
        )
        for sub, predicate in self.subscriptions:
            if predicate is None or predicate(frame):
                sub.queue.put_nowait(frame)
        return actual_turn

    async def wait_for_turn_idle(self, turn_id: str, *, timeout: float | None = None) -> None:
        return None


def _write_profile(root: Path) -> Path:
    profile_root = root / "demo_app" / "profiles"
    profile_root.mkdir(parents=True)
    (profile_root / "config.jsonl").write_text(
        "\n".join(
            [
                '{"kind":"profile","name":"demo_app"}',
                '{"kind":"speech","enabled":true,"provider":"kokoro","voice":"zf_001"}',
                '{"kind":"speech_input","enabled":false,"provider":"funasr"}',
                '{"kind":"vision","no_camera":true,"head_tracker":"none"}',
                '{"kind":"kernel_model","provider":"mock","model":"mock"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return profile_root.parent


def _write_env_key_profile(root: Path) -> Path:
    profile_root = root / "env_key_app" / "profiles"
    profile_root.mkdir(parents=True)
    (profile_root / "config.jsonl").write_text(
        "\n".join(
            [
                '{"kind":"profile","name":"env_key_app"}',
                '{"kind":"kernel_model","provider":"openai","model":"demo","api_key":"env:DEMO_KEY"}',
                '{"kind":"speech","enabled":true,"provider":"kokoro","voice":"zf_001"}',
                '{"kind":"speech_input","enabled":false}',
                '{"kind":"vision","no_camera":true}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return profile_root.parent


@pytest.mark.asyncio
async def test_run_text_turn_emits_reply_action_and_result(tmp_path: Path) -> None:
    """Runner executes one v4 text turn through the mock path."""
    app_root = _write_profile(tmp_path)

    frames = await run_text_turn(profile_path=app_root, text="你好")

    assert any(isinstance(frame, SDKMessageFrame) for frame in frames)
    assert any(
        isinstance(frame, ActionResultFrame) and frame.status == "ok"
        for frame in frames
    )


@pytest.mark.asyncio
async def test_run_text_turn_does_not_require_profile_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Text/mock smoke uses deterministic config even when profile secrets are absent."""
    monkeypatch.delenv("DEMO_KEY", raising=False)
    app_root = _write_env_key_profile(tmp_path)

    frames = await run_text_turn(profile_path=app_root, text="你好")

    assert any(isinstance(frame, SDKMessageFrame) for frame in frames)
    assert any(
        isinstance(frame, ActionResultFrame) and frame.status == "ok"
        for frame in frames
    )


@pytest.mark.asyncio
async def test_run_text_turn_supports_generated_app_project(tmp_path: Path) -> None:
    """Phase 2 entry requires v4 compatibility with generated app projects."""
    app_root = create_app_project(tmp_path / "generated_demo", "generated_demo")

    frames = await run_text_turn(profile_path=app_root, text="你好")

    assert any(isinstance(frame, SDKMessageFrame) for frame in frames)
    assert any(
        isinstance(frame, ActionResultFrame) and frame.status == "ok"
        for frame in frames
    )


@pytest.mark.asyncio
async def test_handle_agent_runs_one_text_turn(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`reachy-mini-agent agent` defaults to the v4 RuntimeSession."""
    app_root = _write_profile(tmp_path)
    from_profile_kwargs: list[dict[str, Any]] = []
    from_profile_paths: list[Path] = []

    def recording_from_profile(
        profile_path: Path,
        *,
        overrides: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> RuntimeSession:
        from_profile_paths.append(Path(profile_path))
        from_profile_kwargs.append(dict(kwargs))
        assert overrides == {}
        return _FakeCLISession()  # type: ignore[return-value]

    monkeypatch.setattr(RuntimeSession, "from_profile", recording_from_profile)
    args = SimpleNamespace(
        app=str(app_root),
        apps_root=tmp_path,
        message="你好",
        thread_id="",
        override=[],
        log_level="INFO",
    )

    await handle_agent(args)

    output = capsys.readouterr().out
    assert "我听到了：你好" in output
    assert from_profile_paths == [app_root / "profiles"]
    assert "client_factory" not in from_profile_kwargs[0]
