"""Pi binary brain host: BrainAgent-shaped lifecycle over `pi --mode rpc`."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
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

# Built-in anthropic catalog does not include proxy model ids like grok-4.5.
_DEFAULT_ANTHROPIC_BASE = "https://api.anthropic.com"


def _default_extension_path() -> Path:
    env = os.environ.get("REACHY_PI_EXT", "").strip()
    if env:
        return Path(env).expanduser()
    for candidate in _DEFAULT_EXT_CANDIDATES:
        if candidate.is_file():
            return candidate
    return _DEFAULT_EXT_CANDIDATES[0]


def prepare_pi_agent_dir_for_model(
    *,
    provider: str,
    model: str,
    base_url: str | None,
    agent_dir: Path | None = None,
) -> Path | None:
    """Stage a temporary ``models.json`` so Pi can use custom base URL / model ids.

    Pi does **not** honor ``ANTHROPIC_BASE_URL`` by itself; custom endpoints and
    non-catalog model ids (e.g. Claude Code gateway + ``grok-4.5``) need
    ``~/.pi/agent/models.json`` or ``PI_CODING_AGENT_DIR``.
    """
    provider_l = (provider or "").strip().lower()
    model_id = (model or "").strip()
    base = (base_url or "").strip().rstrip("/")
    if not provider_l or provider_l == "mock":
        return None

    needs_base = bool(base) and base.rstrip("/") != _DEFAULT_ANTHROPIC_BASE.rstrip("/")
    # Always stage when base_url is custom; also stage anthropic when model looks non-Claude.
    needs_model = False
    if provider_l == "anthropic" and model_id:
        needs_model = not model_id.startswith("claude-")
    if not needs_base and not needs_model:
        return None

    root = Path(agent_dir) if agent_dir is not None else Path(
        tempfile.mkdtemp(prefix="reachy-pi-agent-")
    )
    root.mkdir(parents=True, exist_ok=True)

    if provider_l == "anthropic":
        api = "anthropic-messages"
        api_key_ref = "$ANTHROPIC_AUTH_TOKEN"
        provider_block: dict[str, Any] = {
            "api": api,
            # Prefer bearer token (Claude Code style); API key also accepted by Pi.
            "apiKey": api_key_ref,
            "authHeader": True,
        }
        if base:
            provider_block["baseUrl"] = base
        if needs_model or needs_base:
            provider_block["models"] = [
                {
                    "id": model_id or "custom-model",
                    "name": model_id or "custom-model",
                    "reasoning": True,
                    "input": ["text"],
                    "contextWindow": 200000,
                    "maxTokens": 8192,
                    "cost": {
                        "input": 0,
                        "output": 0,
                        "cacheRead": 0,
                        "cacheWrite": 0,
                    },
                }
            ]
    else:
        # Generic OpenAI-compatible staging for openai/deepseek-style providers.
        api = "openai-completions"
        provider_block = {
            "api": api,
            "apiKey": "$OPENAI_API_KEY",
        }
        if base:
            provider_block["baseUrl"] = base
        provider_block["models"] = [
            {
                "id": model_id or "custom-model",
                "name": model_id or "custom-model",
                "reasoning": False,
                "input": ["text"],
                "contextWindow": 128000,
                "maxTokens": 8192,
                "cost": {
                    "input": 0,
                    "output": 0,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                },
            }
        ]

    payload = {"providers": {provider_l: provider_block}}
    models_path = root / "models.json"
    models_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    LOGGER.info("staged pi models.json at %s (provider=%s model=%s)", models_path, provider_l, model_id)
    return root


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
        stage_models_json: bool = True,
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
        self.stage_models_json = stage_models_json
        self._client = client
        self._owns_client = client is None
        self._started = False
        self._staged_agent_dir: Path | None = None
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

        provider = (self.config.model.provider or "").lower()
        api_key = (self.config.model.api_key or "").strip()
        if api_key:
            # Claude Code uses ANTHROPIC_AUTH_TOKEN (Bearer); Pi also accepts API key.
            env.setdefault("ANTHROPIC_API_KEY", api_key)
            env.setdefault("ANTHROPIC_AUTH_TOKEN", api_key)
            if provider in {"openai", "deepseek"}:
                env.setdefault("OPENAI_API_KEY", api_key)
            if provider == "deepseek":
                env.setdefault("DEEPSEEK_API_KEY", api_key)

        base_url = (self.config.model.base_url or "").strip()
        if base_url:
            # Informational for operators / other tools; Pi itself needs models.json.
            env.setdefault("ANTHROPIC_BASE_URL", base_url)

        # Inherit local proxy settings if the parent shell has them (Claude Code often does).
        for proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"):
            if proxy_key not in env and os.environ.get(proxy_key):
                env[proxy_key] = os.environ[proxy_key]

        if self.stage_models_json and "PI_CODING_AGENT_DIR" not in env:
            staged = prepare_pi_agent_dir_for_model(
                provider=provider,
                model=self.config.model.model or "",
                base_url=base_url or None,
                agent_dir=self._staged_agent_dir,
            )
            if staged is not None:
                self._staged_agent_dir = staged
                env["PI_CODING_AGENT_DIR"] = str(staged)

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
    """Pull *spoken* text deltas out of Pi agent session events.

    Intentionally ignores ``thinking_delta`` so extended-thinking models do not
    leak chain-of-thought into the speech / Live2D bubble path.
    """
    if event.get("type") != "message_update":
        return ""
    ame = event.get("assistantMessageEvent") or event.get("assistant_message_event") or {}
    if not isinstance(ame, dict):
        return ""
    if ame.get("type") == "text_delta":
        return str(ame.get("delta") or "")
    if ame.get("type") == "text" and ame.get("text"):
        return str(ame.get("text") or "")
    return ""


__all__ = [
    "AssistantMessage",
    "PiBinaryBrain",
    "PiResultMessage",
    "TextBlock",
    "prepare_pi_agent_dir_for_model",
]
