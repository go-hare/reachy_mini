"""Claude Agent SDK-backed Brain facade for the v4 runtime."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from reachy_mini.action_runtime.registry import ActionRegistry

from .config import AgentConfig
from .mcp_server import (
    ActionRunner,
    action_allowed_tool_names,
    create_action_mcp_server,
    create_robot_mcp_server,
    require_claude_agent_sdk,
    robot_allowed_tool_names,
)


@dataclass(frozen=True)
class BrainTurnInput:
    """Input supplied to Claude Agent SDK for one completed turn."""

    text: str
    turn_id: str
    context: dict[str, Any] = field(default_factory=dict)


class SDKClient(Protocol):
    """Subset of ClaudeSDKClient used by BrainAgent."""

    async def connect(self) -> None:
        """Connect the client."""

    async def query(self, prompt: str, session_id: str = "default") -> None:
        """Send one user prompt into the SDK session."""

    def receive_response(self) -> AsyncIterator[Any]:
        """Receive messages until the SDK result message for this turn."""

    async def interrupt(self) -> None:
        """Interrupt the active SDK response."""

    async def stop_task(self, task_id: str) -> None:
        """Stop an SDK background task."""

    async def disconnect(self) -> None:
        """Disconnect the client."""


ClientFactory = Callable[[Any], SDKClient]


class BrainAgent:
    """Single Claude Agent SDK brain loop."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        registry: ActionRegistry,
        run_action: ActionRunner,
        owner_id: str = "main-agent",
        client_factory: ClientFactory | None = None,
        cwd: Path | str | None = None,
        system_prompt_append: str = "",
        robot_tools: Any | None = None,
        action_tools_enabled: bool = True,
    ) -> None:
        """Create a Brain agent bound to an ActionRuntime MCP facade."""
        self.config = config
        self.registry = registry
        self.run_action = run_action
        self.owner_id = owner_id
        self.cwd = Path(cwd).resolve() if cwd is not None else Path.cwd()
        self.system_prompt_append = str(system_prompt_append or "").strip()
        self.robot_tools = robot_tools
        self.action_tools_enabled = action_tools_enabled
        self._client_factory = client_factory
        self._client: SDKClient | None = None
        self.options = self._build_options()

    async def start(self) -> None:
        """Connect the underlying ClaudeSDKClient once per runtime session."""
        if self._client is not None:
            return
        self._client = self._make_client(self.options)
        await self._client.connect()

    async def stop(self) -> None:
        """Disconnect the underlying SDK client."""
        client = self._client
        if client is None:
            return
        self._client = None
        await client.disconnect()

    async def reset(self, *, timeout_s: float = 12.0) -> None:
        """Drop the current SDK client and force-close a stuck CLI process."""
        client = self._client
        if client is None:
            return
        self._client = None
        try:
            await asyncio.wait_for(client.disconnect(), timeout=timeout_s)
        except Exception:
            await self._force_close_client_process(client)

    async def interrupt(self) -> None:
        """Interrupt the current SDK response if connected."""
        if self._client is not None:
            await self._client.interrupt()

    async def stop_task(self, task_id: str) -> None:
        """Stop a Claude Agent SDK background task."""
        if self._client is not None:
            await self._client.stop_task(task_id)

    async def run_turn(self, turn_input: BrainTurnInput) -> AsyncIterator[Any]:
        """Send one turn to Claude Agent SDK and yield native SDK messages."""
        await self.start()
        assert self._client is not None
        await self._client.query(
            self._format_prompt(turn_input),
            session_id=turn_input.turn_id or "default",
        )
        async for message in self._client.receive_response():
            yield message

    def _make_client(self, options: Any) -> SDKClient:
        if self._client_factory is not None:
            return self._client_factory(options)
        require_claude_agent_sdk()
        from claude_agent_sdk import ClaudeSDKClient

        return ClaudeSDKClient(options=options)

    def _build_options(self) -> Any:
        require_claude_agent_sdk()
        from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions

        mcp_servers: dict[str, Any] = {}
        allowed_tools: list[str] = []
        agent_mcp_servers: list[str] = []
        if self.action_tools_enabled:
            mcp_servers["reachy_actions"] = create_action_mcp_server(
                registry=self.registry,
                run_action=self.run_action,
                owner_id=self.owner_id,
            )
            allowed_tools.extend(action_allowed_tool_names(self.registry))
            agent_mcp_servers.append("reachy_actions")
        if self.robot_tools is not None:
            mcp_servers["reachy_robot"] = create_robot_mcp_server(
                robot_tools=self.robot_tools,
            )
            allowed_tools.extend(robot_allowed_tool_names())
            agent_mcp_servers.append("reachy_robot")
        agents = {
            "reachy_background": AgentDefinition(
                description="Run long-running Reachy Mini observations or plans in the background.",
                prompt=_load_prompt("worker.md"),
                tools=allowed_tools,
                mcpServers=agent_mcp_servers,
                background=True,
                permissionMode="dontAsk",
                effort="high",
            )
        }
        return ClaudeAgentOptions(
            model=self.config.model.model or None,
            system_prompt=self._system_prompt(),
            cwd=self.cwd,
            permission_mode="dontAsk",
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            agents=agents,
            env=self._sdk_env(),
        )

    def _system_prompt(self) -> str:
        configured = self.config.model.system_prompt_path
        if configured:
            path = Path(configured)
            if not path.is_absolute():
                path = self.cwd / path
            if path.is_file():
                return self._append_system_prompt(path.read_text(encoding="utf-8"))
        return self._append_system_prompt(_load_prompt("system.md"))

    def _append_system_prompt(self, base: str) -> str:
        if not self.system_prompt_append:
            return base
        return f"{base.rstrip()}\n\n{self.system_prompt_append}\n"

    def _sdk_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self.config.model.base_url:
            env["ANTHROPIC_BASE_URL"] = self.config.model.base_url
        if not self.config.model.api_key:
            return env
        provider = self.config.model.provider.lower()
        if provider in {"anthropic", "claude", "openai", "deepseek"}:
            env["ANTHROPIC_API_KEY"] = self.config.model.api_key
            if self.config.model.base_url:
                env["ANTHROPIC_AUTH_TOKEN"] = self.config.model.api_key
        return env

    def _format_prompt(self, turn_input: BrainTurnInput) -> str:
        if not turn_input.context:
            return turn_input.text
        return (
            f"{turn_input.text}\n\n"
            "<reachy_runtime_context>\n"
            f"{turn_input.context}\n"
            "</reachy_runtime_context>"
        )

    async def _force_close_client_process(self, client: SDKClient) -> None:
        transport = getattr(client, "_transport", None)
        process = getattr(transport, "_process", None)
        if process is None:
            return

        if getattr(process, "returncode", None) is None:
            with contextlib.suppress(Exception):
                process.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=1.0)

        if getattr(process, "returncode", None) is None:
            with contextlib.suppress(Exception):
                process.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=1.0)


def _load_prompt(name: str) -> str:
    path = Path(__file__).with_name("prompts") / name
    return path.read_text(encoding="utf-8")
