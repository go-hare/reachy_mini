"""SC-8: reachy-mini-agent agent defaults to the v4 RuntimeSession."""

from __future__ import annotations

import argparse
import asyncio
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from reachy_mini.pipeline.session import RuntimeSession
from reachy_mini.pipeline.frames import SDKMessageFrame
from reachy_mini.runtime import main as cli_main
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


@pytest.mark.asyncio
async def test_agent_command_uses_runtime_session_without_brain_kernel(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent command runs a v4 text turn and never instantiates the old kernel."""
    app_root = create_app_project(tmp_path / "cli_probe", "cli_probe")
    brain_kernel_calls: list[tuple[Any, ...]] = []
    runtime_session_calls: list[Path] = []

    class LegacyKernelProbe:
        def __init__(self, *args: Any, **_kwargs: Any) -> None:
            brain_kernel_calls.append(args)

    fake_core = types.ModuleType("reachy_mini.core")
    fake_agent = types.ModuleType("reachy_mini.core.agent")
    setattr(fake_agent, "Brain" + "Kernel", LegacyKernelProbe)
    monkeypatch.setitem(sys.modules, "reachy_mini.core", fake_core)
    monkeypatch.setitem(sys.modules, "reachy_mini.core.agent", fake_agent)
    from_profile_kwargs: list[dict[str, Any]] = []

    def recording_from_profile(
        profile_path: Path,
        *,
        overrides: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> RuntimeSession:
        runtime_session_calls.append(Path(profile_path))
        from_profile_kwargs.append(dict(kwargs))
        assert overrides == {}
        return _FakeCLISession()  # type: ignore[return-value]

    monkeypatch.setattr(RuntimeSession, "from_profile", recording_from_profile)

    await cli_main.handle_agent(
        SimpleNamespace(
            app=str(app_root),
            apps_root=tmp_path,
            message="hi",
            thread_id="T1",
            override=[],
            log_level="INFO",
        )
    )

    output = capsys.readouterr().out
    assert "我听到了：hi" in output
    assert runtime_session_calls == [app_root / "profiles"]
    assert "client_factory" not in from_profile_kwargs[0]
    assert brain_kernel_calls == []
    assert "DEPRECATION" not in output


def test_cli_has_no_v4_subcommand_or_legacy_agent_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 2 CLI exposes agent/web directly and rejects old runtime flags."""
    monkeypatch.setattr(sys, "argv", ["reachy-mini-agent", "v4"])
    with pytest.raises(SystemExit):
        cli_main.parse_args()

    legacy_flags = [
        "--" + "provider",
        "--" + "model",
        "--api" + "-key",
        "--kernel" + "-provider",
        "--turn" + "-id",
    ]
    for flag in legacy_flags:
        monkeypatch.setattr(
            sys,
            "argv",
            ["reachy-mini-agent", "agent", "profiles/sim_front_app", flag, "x"],
        )
        with pytest.raises(SystemExit):
            cli_main.parse_args()

    monkeypatch.setattr(
        sys,
        "argv",
        ["reachy-mini-agent", "agent", "profiles/sim_front_app", "--message", "hi"],
    )
    args = cli_main.parse_args()
    assert isinstance(args, argparse.Namespace)
    assert args.command == "agent"
    assert args.message == "hi"
    assert args.thread_id == ""

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "reachy-mini-agent",
            "agent",
            "profiles/sim_front_app",
            "--message",
            "hi",
            "--thread-id",
            "T1",
        ],
    )
    args = cli_main.parse_args()
    assert args.thread_id == "T1"


def test_interactive_agent_keeps_real_sdk_default(tmp_path: Path) -> None:
    """CLI session construction never injects the offline SDK client by default."""
    app_root = create_app_project(tmp_path / "interactive_probe", "interactive_probe")
    session = cli_main._build_agent_session(
        app_root / "profiles",
        overrides={},
    )

    assert session.agent._client_factory is None
