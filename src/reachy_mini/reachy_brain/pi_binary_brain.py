"""Pi binary brain host: BrainAgent-shaped lifecycle over `pi --mode rpc`."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent import BrainTurnInput
from .config import AgentConfig
from .pi_rpc_client import PiRpcClient, PiRpcClientOptions

LOGGER = logging.getLogger(__name__)

_DEFAULT_EXT_CANDIDATES = (
    Path(r"D:\work\py\pi\packages\robot-agent\extensions\robot-tools.ts"),
    Path(__file__).resolve().parents[4] / "pi" / "packages" / "robot-agent" / "extensions" / "robot-tools.ts",
)


def _default_extension_path() -> Path:
    env = os.environ.get("REACHY_PI_EXT", "").strip()
    if env:
        return Path(env).expanduser()
    for candidate in _DEFAULT_EXT_CANDIDATES:
        if candidate.is_file():
            return candidate
    return _DEFAULT_EXT_CANDIDATES[0]


@dataclass
class TextBlock:
    """Duck-type Claude SDK TextBlock for speech extraction."""

    text: str


@dataclass
class AssistantMessage:
    """Duck-type Claude SDK AssistantMessage for pipeline compatibility."""

    content: list[Any]
    model: str = "pi-rpc"
    session_id: str = "default"


@dataclass
class PiResultMessage:
    """Turn completion marker (not required by speech path)."""

    subtype: str = "success"
    session_id: str = "default"
    result: str = ""


class PiBinaryBrain:
    """Single-agent brain backed by the Pi coding-agent RPC binary."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        extension_path: str | Path | None = None,
        pi_bin: str | None = None,
        runtime_url: str | None = None,
        runtime_token: str | None = None,
        cwd: Path | str | None = None,
        system_prompt_append: str = "",
        tool_bridge: Any | None = None,
        extra_env: dict[str, str] | None = None,
        client: PiRpcClient | None = None,
    ) -> None:
        self.config = config
        self.extension_path = Path(extension_path) if extension_path else _default_extension_path()
        self.pi_bin = pi_bin or os.environ.get("REACHY_PI_BIN") or "pi"
        self.runtime_url = (
            runtime_url
            or os.environ.get("REACHY_RUNTIME_URL")
            or "http://127.0.0.1:8787"
        )
        self.runtime_token = runtime_token or os.environ.get("REACHY_RUNTIME_TOKEN")
        self.cwd = Path(cwd).resolve() if cwd is not None else Path.cwd()
        self.system_prompt_append = str(system_prompt_append or "").strip()
        self.tool_bridge = tool_bridge
        self.extra_env = dict(extra_env or {})
        self._client = client
        self._owns_client = client is None
        self._started = False
        # Robot tools live in the TS extension, not Claude MCP.
        self.robot_tools = None

    async def start(self) -> None:
        """Start the Pi RPC subprocess once per session."""
        if self._started:
            return
        if self._client is None:
            self._client = PiRpcClient(self._client_options())
        if not self._client.is_running:
            await self._client.start()
        self._started = True

    async def stop(self) -> None:
        """Stop the Pi RPC subprocess."""
        self._started = False
        client = self._client
        if client is None:
            return
        if self._owns_client:
            await client.stop()
            self._client = None

    async def reset(self, *, timeout_s: float = 12.0) -> None:
        """Restart conversation / process after a stuck or failed turn."""
        del timeout_s
        client = self._client
        if client is None:
            self._started = False
            return
        try:
            await client.abort()
        except Exception:
            LOGGER.debug("pi abort during reset failed", exc_info=True)
        try:
            await client.new_session()
            return
        except Exception:
            LOGGER.debug(
                "pi new_session during reset failed; restarting process",
                exc_info=True,
            )
        if self._owns_client:
            await client.stop()
            self._client = PiRpcClient(self._client_options())
            await self._client.start()
            self._started = True

    async def interrupt(self) -> None:
        """Abort the active Pi agent turn."""
        if self._client is not None and self._client.is_running:
            try:
                await self._client.abort()
            except Exception:
                LOGGER.debug("pi interrupt abort failed", exc_info=True)

    async def stop_task(self, task_id: str) -> None:
        """No dedicated Pi task API; best-effort abort."""
        del task_id
        await self.interrupt()

    async def run_turn(self, turn_input: BrainTurnInput) -> AsyncIterator[Any]:
        """Prompt Pi and yield pipeline-compatible assistant messages."""
        await self.start()
        assert self._client is not None

        if self.tool_bridge is not None:
            setter = getattr(self.tool_bridge, "set_turn_id", None)
            if callable(setter):
                setter(turn_input.turn_id)

        prompt = self._format_prompt(turn_input)
        text_parts: list[str] = []
        events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        yielded_stream = False

        def _on_event(event: dict[str, Any]) -> None:
            events.put_nowait(event)
            if event.get("type") == "agent_settled":
                events.put_nowait(None)

        unsubscribe = self._client.on_event(_on_event)
        try:
            response = await self._client.prompt(prompt)
            if response.get("success") is False:
                raise RuntimeError(response.get("error") or "pi prompt failed")

            while True:
                try:
                    event = await asyncio.wait_for(events.get(), timeout=120.0)
                except asyncio.TimeoutError as exc:
                    raise TimeoutError("timeout waiting for pi agent_settled") from exc
                if event is None:
                    break
                delta = _extract_text_delta(event)
                if delta:
                    text_parts.append(delta)
                    yielded_stream = True
                    yield AssistantMessage(
                        content=[TextBlock(text=delta)],
                        model=self.config.model.model or "pi-rpc",
                        session_id=turn_input.turn_id or "default",
                    )
        finally:
            unsubscribe()
            if self.tool_bridge is not None:
                clearer = getattr(self.tool_bridge, "clear_turn_id", None)
                if callable(clearer):
                    clearer()

        full = "".join(text_parts).strip()
        if not yielded_stream:
            if not full:
                try:
                    last = await self._client.send({"type": "get_last_assistant_text"})
                    if last.get("success"):
                        full = str((last.get("data") or {}).get("text") or "").strip()
                except Exception:
                    LOGGER.debug("get_last_assistant_text failed", exc_info=True)
            if full:
                yield AssistantMessage(
                    content=[TextBlock(text=full)],
                    model=self.config.model.model or "pi-rpc",
                    session_id=turn_input.turn_id or "default",
                )

        yield PiResultMessage(
            subtype="success",
            session_id=turn_input.turn_id or "default",
            result=full,
        )

    def _client_options(self) -> PiRpcClientOptions:
        env = {
            "REACHY_RUNTIME_URL": self.runtime_url,
            "REACHY_BRIDGE_MODE": "http",
            **self.extra_env,
        }
        if self.runtime_token:
            env["REACHY_RUNTIME_TOKEN"] = self.runtime_token
        if self.config.model.api_key:
            env.setdefault("ANTHROPIC_API_KEY", self.config.model.api_key)
            provider = self.config.model.provider.lower()
            if provider in {"openai", "deepseek"}:
                env.setdefault("OPENAI_API_KEY", self.config.model.api_key)
        if self.config.model.base_url:
            env.setdefault("ANTHROPIC_BASE_URL", self.config.model.base_url)

        provider = self.config.model.provider or None
        model = self.config.model.model or None
        return PiRpcClientOptions(
            pi_bin=self.pi_bin,
            extension_path=str(self.extension_path),
            cwd=self.cwd,
            env=env,
            provider=provider if provider and provider != "mock" else None,
            model=model or None,
            no_session=True,
        )

    def _format_prompt(self, turn_input: BrainTurnInput) -> str:
        parts = [turn_input.text]
        if turn_input.context:
            parts.append(
                "<reachy_runtime_context>\n"
                f"{turn_input.context}\n"
                "</reachy_runtime_context>"
            )
        if self.system_prompt_append:
            parts.append(
                "<reachy_system_hint>\n"
                f"{self.system_prompt_append}\n"
                "</reachy_system_hint>"
            )
        return "\n\n".join(parts)


def _extract_text_delta(event: dict[str, Any]) -> str:
    """Pull text deltas out of Pi agent session events."""
    if event.get("type") != "message_update":
        return ""
    ame = event.get("assistantMessageEvent") or event.get("assistant_message_event") or {}
    if not isinstance(ame, dict):
        return ""
    if ame.get("type") in {"text_delta", "thinking_delta"}:
        return str(ame.get("delta") or "")
    if ame.get("type") == "text" and ame.get("text"):
        return str(ame.get("text") or "")
    return ""


__all__ = [
    "AssistantMessage",
    "PiBinaryBrain",
    "PiResultMessage",
    "TextBlock",
]
