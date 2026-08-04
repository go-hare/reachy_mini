"""Unit tests for Pi JSONL RPC client framing (fake process pipes)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from reachy_mini.reachy_brain.pi_binary_brain import (
    AssistantMessage,
    PiBinaryBrain,
    TextBlock,
    _extract_text_delta,
    prepare_pi_agent_dir_for_model,
)
from reachy_mini.reachy_brain.config import (
    AgentConfig,
    ModelConfig,
    SpeechConfig,
    SpeechInputConfig,
    VisionConfig,
)
from reachy_mini.reachy_brain.pi_rpc_client import (
    PiRpcClient,
    PiRpcClientOptions,
    resolve_pi_executable,
)


def extract_text_blocks(message: Any) -> list[str]:
    """Local helper mirroring pipecat_bridge (avoids heavy pipeline import graph)."""
    if type(message).__name__ != "AssistantMessage":
        return []
    texts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if type(block).__name__ == "TextBlock" or hasattr(block, "text"):
            text = str(getattr(block, "text", "") or "").strip()
            if text:
                texts.append(text)
    return texts


class _FakeStdin:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    def write(self, data: bytes) -> None:
        text = data.decode("utf-8")
        self.lines.append(text)
        self._queue.put_nowait(text)

    async def drain(self) -> None:
        return None


class _FakeStdout:
    def __init__(self) -> None:
        self._chunks: asyncio.Queue[bytes | None] = asyncio.Queue()

    def push(self, text: str) -> None:
        self._chunks.put_nowait(text.encode("utf-8"))

    def close(self) -> None:
        self._chunks.put_nowait(None)

    async def read(self, _n: int = 4096) -> bytes:
        item = await self._chunks.get()
        return b"" if item is None else item


class _FakeStderr:
    async def read(self, _n: int = 4096) -> bytes:
        await asyncio.sleep(3600)
        return b""


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout()
        self.stderr = _FakeStderr()
        self.returncode: int | None = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode


@pytest.mark.asyncio
async def test_rpc_client_prompt_response_and_events(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeProcess()

    async def _fake_exec(*_args: Any, **_kwargs: Any) -> _FakeProcess:
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    client = PiRpcClient(PiRpcClientOptions(pi_bin="pi", extension_path="ext.ts"))
    # Bypass settle sleep crash check by keeping returncode None
    start_task = asyncio.create_task(client.start())
    await asyncio.sleep(0.05)
    # start() sleeps 0.15 then checks returncode
    await start_task

    events: list[dict[str, Any]] = []
    client.on_event(lambda e: events.append(e))

    async def _reply() -> None:
        # Wait until prompt line arrives
        for _ in range(50):
            if fake.stdin.lines:
                break
            await asyncio.sleep(0.02)
        line = fake.stdin.lines[-1]
        cmd = json.loads(line)
        req_id = cmd["id"]
        # response first
        fake.stdout.push(
            json.dumps({"id": req_id, "type": "response", "command": "prompt", "success": True})
            + "\n"
        )
        # then streaming events
        fake.stdout.push(
            json.dumps(
                {
                    "type": "message_update",
                    "assistantMessageEvent": {"type": "text_delta", "delta": "你好"},
                }
            )
            + "\n"
        )
        fake.stdout.push(json.dumps({"type": "agent_settled"}) + "\n")

    settled_task = asyncio.create_task(client.wait_for_settled(timeout=2.0))
    reply_task = asyncio.create_task(_reply())
    response = await client.prompt("hello")
    settled = await settled_task
    await reply_task

    assert response["success"] is True
    assert settled["type"] == "agent_settled"
    assert any(e.get("type") == "message_update" for e in events)

    await client.stop()


def test_extract_text_delta() -> None:
    assert (
        _extract_text_delta(
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "hi"},
            }
        )
        == "hi"
    )
    # thinking must not become speech
    assert (
        _extract_text_delta(
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "thinking_delta", "delta": "secret"},
            }
        )
        == ""
    )
    assert _extract_text_delta({"type": "agent_settled"}) == ""


def test_prepare_pi_agent_dir_for_custom_anthropic(tmp_path) -> None:
    staged = prepare_pi_agent_dir_for_model(
        provider="anthropic",
        model="grok-4.5",
        base_url="http://204.44.121.220:8317",
        agent_dir=tmp_path / "agent",
    )
    assert staged is not None
    data = json.loads((staged / "models.json").read_text(encoding="utf-8"))
    anth = data["providers"]["anthropic"]
    assert anth["baseUrl"] == "http://204.44.121.220:8317"
    assert anth["authHeader"] is True
    assert any(m["id"] == "grok-4.5" for m in anth["models"])


def test_prepare_pi_agent_dir_skips_stock_claude() -> None:
    assert (
        prepare_pi_agent_dir_for_model(
            provider="anthropic",
            model="claude-sonnet-4-6",
            base_url="https://api.anthropic.com",
        )
        is None
    )


def test_client_options_sets_auth_token_and_stages_models(tmp_path) -> None:
    config = AgentConfig(
        model=ModelConfig(
            provider="anthropic",
            model="grok-4.5",
            api_key="sk-test",
            base_url="http://proxy.example:8317",
        ),
        speech=SpeechConfig(),
        speech_input=SpeechInputConfig(),
        vision=VisionConfig(),
        extras={},
    )
    brain = PiBinaryBrain(config=config, cwd=tmp_path)
    opts = brain._client_options()
    assert opts.env.get("ANTHROPIC_AUTH_TOKEN") == "sk-test"
    assert opts.env.get("ANTHROPIC_API_KEY") == "sk-test"
    assert opts.env.get("ANTHROPIC_BASE_URL") == "http://proxy.example:8317"
    assert "PI_CODING_AGENT_DIR" in opts.env
    staged = Path(opts.env["PI_CODING_AGENT_DIR"])
    assert (staged / "models.json").is_file()


def test_resolve_pi_executable_uses_which(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fake = tmp_path / "pi.CMD"
    fake.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(
        "reachy_mini.reachy_brain.pi_rpc_client.shutil.which",
        lambda name: str(fake) if name in {"pi", "pi.cmd", "pi.CMD"} else None,
    )
    resolved = resolve_pi_executable("pi")
    assert resolved == str(fake)


def test_build_command_resolves_pi_bin(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fake = tmp_path / "pi.cmd"
    fake.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(
        "reachy_mini.reachy_brain.pi_rpc_client.resolve_pi_executable",
        lambda _bin=None: str(fake),
    )
    client = PiRpcClient(
        PiRpcClientOptions(pi_bin="pi", extension_path="ext.ts", no_session=True)
    )
    cmd = client._build_command()
    assert cmd[0] == str(fake)
    assert cmd[1:4] == ["--mode", "rpc", "--no-session"]
    assert "-e" in cmd and "ext.ts" in cmd


def test_duck_type_assistant_message_extractable() -> None:
    msg = AssistantMessage(content=[TextBlock(text="  你好  ")], session_id="t1")
    assert extract_text_blocks(msg) == ["你好"]


@pytest.mark.asyncio
async def test_pi_binary_brain_run_turn_with_injected_client() -> None:
    class _StubClient:
        def __init__(self) -> None:
            self._listeners: list = []
            self.is_running = True

        def on_event(self, listener):
            self._listeners.append(listener)
            return lambda: self._listeners.remove(listener)

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        async def prompt(self, message: str) -> dict[str, Any]:
            for listener in list(self._listeners):
                listener(
                    {
                        "type": "message_update",
                        "assistantMessageEvent": {"type": "text_delta", "delta": "pong"},
                    }
                )
                listener({"type": "agent_settled"})
            return {"success": True, "command": "prompt"}

        async def abort(self) -> dict[str, Any]:
            return {"success": True}

        async def new_session(self) -> dict[str, Any]:
            return {"success": True, "data": {"cancelled": False}}

        async def send(self, command: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
            return {"success": True, "data": {"text": "pong"}}

    config = AgentConfig(
        model=ModelConfig(provider="mock", model="mock-model"),
        speech=SpeechConfig(),
        speech_input=SpeechInputConfig(),
        vision=VisionConfig(),
        extras={},
    )
    brain = PiBinaryBrain(config=config, client=_StubClient())
    from reachy_mini.reachy_brain.agent import BrainTurnInput

    messages = []
    async for msg in brain.run_turn(BrainTurnInput(text="hi", turn_id="turn_1")):
        messages.append(msg)
    assert any(type(m).__name__ == "AssistantMessage" for m in messages)
    texts = []
    for m in messages:
        texts.extend(extract_text_blocks(m))
    assert "pong" in texts
