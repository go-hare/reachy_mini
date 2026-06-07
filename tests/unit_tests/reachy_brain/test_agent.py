"""Tests for the Claude Agent SDK brain facade."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reachy_mini.action_runtime.library import create_builtin_registry  # noqa: E402
from reachy_mini.reachy_brain.agent import BrainAgent  # noqa: E402
from reachy_mini.reachy_brain.config import (  # noqa: E402
    AgentConfig,
    ModelConfig,
    SpeechConfig,
    SpeechInputConfig,
    VisionConfig,
)


async def _unused_action_runner(_spec):
    raise AssertionError("action runner should not be called")


class _StuckProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int | None:
        await asyncio.sleep(0)
        return self.returncode


class _StuckTransport:
    def __init__(self, process: _StuckProcess) -> None:
        self._process = process


class _StuckDisconnectClient:
    def __init__(self) -> None:
        self.process = _StuckProcess()
        self._transport = _StuckTransport(self.process)

    async def connect(self) -> None:
        return None

    async def query(self, _prompt: str, session_id: str = "default") -> None:
        return None

    def receive_response(self):
        if False:
            yield None

    async def interrupt(self) -> None:
        return None

    async def stop_task(self, _task_id: str) -> None:
        return None

    async def disconnect(self) -> None:
        await asyncio.sleep(60)


def test_agent_options_pass_profile_endpoint_and_key_to_sdk_env() -> None:
    """Profile model endpoint settings must reach the bundled Claude CLI."""
    registry = create_builtin_registry()
    config = AgentConfig(
        model=ModelConfig(
            provider="openai",
            model="claude-opus-4.6",
            base_url="http://gateway.example/cc",
            api_key="test-secret",
        ),
        speech=SpeechConfig(),
        speech_input=SpeechInputConfig(enabled=False),
        vision=VisionConfig(),
        extras={},
    )

    agent = BrainAgent(
        config=config,
        registry=registry,
        run_action=_unused_action_runner,
    )

    assert agent.options.env["ANTHROPIC_BASE_URL"] == "http://gateway.example/cc"
    assert agent.options.env["ANTHROPIC_API_KEY"] == "test-secret"
    assert agent.options.env["ANTHROPIC_AUTH_TOKEN"] == "test-secret"


@pytest.mark.asyncio
async def test_agent_reset_force_closes_stuck_sdk_process() -> None:
    """A stuck SDK disconnect should not leave the CLI child attached."""
    registry = create_builtin_registry()
    client = _StuckDisconnectClient()
    config = AgentConfig(
        model=ModelConfig(provider="openai", model="claude-opus-4.6"),
        speech=SpeechConfig(),
        speech_input=SpeechInputConfig(enabled=False),
        vision=VisionConfig(),
        extras={},
    )
    agent = BrainAgent(
        config=config,
        registry=registry,
        run_action=_unused_action_runner,
        client_factory=lambda _options: client,
    )

    await agent.start()
    await agent.reset(timeout_s=0.01)

    assert agent._client is None
    assert client.process.terminated is True
    assert client.process.killed is True
