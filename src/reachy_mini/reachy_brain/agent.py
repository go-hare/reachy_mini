"""Claude Agent SDK-backed Brain facade for the v4 runtime."""

from __future__ import annotations

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
    require_claude_agent_sdk,
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
    ) -> None:
        """Create a Brain agent bound to an ActionRuntime MCP facade."""
        self.config = config
        self.registry = registry
        self.run_action = run_action
        self.owner_id = owner_id
        self.cwd = Path(cwd).resolve() if cwd is not None else Path.cwd()
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
        if self._client is None:
            return
        await self._client.disconnect()
        self._client = None

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

        action_server = create_action_mcp_server(
            registry=self.registry,
            run_action=self.run_action,
            owner_id=self.owner_id,
        )
        allowed_tools = action_allowed_tool_names(self.registry)
        agents = {
            "reachy_background": AgentDefinition(
                description="Run long-running Reachy Mini observations or plans in the background.",
                prompt=_load_prompt("worker.md"),
                tools=allowed_tools,
                mcpServers=["reachy_actions"],
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
            mcp_servers={"reachy_actions": action_server},
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
                return path.read_text(encoding="utf-8")
        return _load_prompt("system.md")

    def _sdk_env(self) -> dict[str, str]:
        if not self.config.model.api_key:
            return {}
        provider = self.config.model.provider.lower()
        if provider in {"anthropic", "claude"}:
            return {"ANTHROPIC_API_KEY": self.config.model.api_key}
        return {}

    def _format_prompt(self, turn_input: BrainTurnInput) -> str:
        if not turn_input.context:
            return turn_input.text
        return (
            f"{turn_input.text}\n\n"
            "<reachy_runtime_context>\n"
            f"{turn_input.context}\n"
            "</reachy_runtime_context>"
        )


def _load_prompt(name: str) -> str:
    path = Path(__file__).with_name("prompts") / name
    return path.read_text(encoding="utf-8")
