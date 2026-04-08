from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import ccmini.factory as factory_module
import ccmini.frontend_host as frontend_host_module
import ccmini.engine.query as query_module
import ccmini.engine.query_engine as query_engine_module
import ccmini.delegation.background as background_module
import ccmini.delegation.subagent as subagent_module
import ccmini.kairos.dream as dream_module
import ccmini.services.away_summary as away_summary_module
import ccmini.services.auto_dream as auto_dream_module
import ccmini.services.prompt_suggestion as prompt_suggestion_module
import ccmini.tools.plan_mode as plan_mode_module
from ccmini.agent import Agent, AgentConfig, ConversationRecovery, ToolProfile
from ccmini.auth import load_auth_state
from ccmini.bridge import BridgeConfig, BridgeServer, RemoteExecutorHost, create_remote_executor_host
from ccmini.config import CLIConfig, load_config, load_profile, sync_remote_config
from ccmini.commands import SlashCommand
from ccmini.commands.types import Command, CommandSource, CommandType
from ccmini.embedded import HostEvent, HostToolResult
from ccmini.frontend_host import _build_ready_payload, _find_open_port, _port_is_available
from ccmini.hooks import PostSamplingContext, PostSamplingHook
from ccmini.hooks.runner import HookRunner
from ccmini.hooks.user_scripts import load_hook_config
from ccmini.kairos.core import check_directory_trust
from ccmini.kairos.proactive import IdleLevel, ProactiveSuggestionEngine
from ccmini.messages import (
    CompletionEvent,
    ErrorEvent,
    ImageBlock,
    Message,
    PromptSuggestionEvent,
    SpeculationEvent,
    TextBlock,
    TextEvent,
    ToolUseBlock,
    ToolResultBlock,
    assistant_message,
    system_message,
    user_message,
)
from ccmini.permissions import BashCommandAnalyzer, PermissionChecker, PermissionConfig, PermissionMode, RiskLevel
from ccmini.delegation.multi_agent import AgentTool
from ccmini.delegation.builtin_agents import WORKER_AGENT
from ccmini.delegation.tasks import TaskManager
from ccmini.delegation.tasks import TaskType
from ccmini.delegation.teammate import (
    PersistentTeammate,
    SharedTaskList,
    Team,
    TeamConfig,
    TeammateIdentity,
    TeammateState,
    TeammateStatus,
)
from ccmini.delegation.mailbox import MemoryMailbox
from ccmini.providers import ProviderConfig
from ccmini.providers.compatible import OpenAICompatibleProvider
from ccmini.tool import ToolUseContext
from ccmini.usage import UsageRecord
from ccmini.delegation.team_files import get_task_board_path, write_team_file
from ccmini.tools.list_peers import _get_sessions_dir
from ccmini.tools.bash import BashTool
from ccmini.tools.file_read import FileReadTool
from ccmini.tools.file_write import FileWriteTool
from ccmini.tools.send_message import SendMessageTool
from ccmini.tools.task_tools import TaskBoard, TaskCreateTool, TaskGetTool, TaskListTool, TaskOutputTool, TaskUpdateTool
from ccmini.tools.team import TeamCreateTool, TaskStopTool
from ccmini.tools import send_message as send_message_module

extract_memories_module = importlib.import_module("ccmini.services.extract_memories")


class _DummyRunner:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[object, object]] = []
        self.cancelled: list[str] = []
        self.status_by_id: dict[str, object] = {}

    def send_message(self, *args: object, **kwargs: object) -> bool:
        recipient = args[0] if args else kwargs.get("task_id")
        message = args[1] if len(args) > 1 else kwargs.get("message")
        self.sent_messages.append((recipient, message))
        return True

    def list_active(self) -> list[object]:
        return []

    def resolve_task_ref(self, *args: object, **kwargs: object) -> None:
        return None

    def get_status(self, task_id: object, *args: object, **kwargs: object) -> object | None:
        del args, kwargs
        return self.status_by_id.get(str(task_id))

    def cancel(self, task_id: str) -> bool:
        self.cancelled.append(task_id)
        return True


class _ToolContext:
    turn_id = "turn-1"
    agent_id = "agent-1"
    extras: dict[str, object] = {}


class _DummyAgent:
    def __init__(self) -> None:
        self._listeners: list[object] = []
        self._messages: list[Message] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def on_event(self, callback: object) -> object:
        self._listeners.append(callback)

        def _unsubscribe() -> None:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass

        return _unsubscribe

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)


class _DummyProvider:
    model_name = "dummy-model"


class _NamedTool:
    def __init__(self, name: str, aliases: tuple[str, ...] = ()) -> None:
        self.name = name
        self.aliases = aliases


def _prepare_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    for key in list(os.environ):
        if key.startswith(("CCMINI_", "MINI_TRUST_")) or key in {
            "MINI_AGENT_HOME",
            "MINI_AGENT_BRIDGE_BASE_URL",
            "MINI_AGENT_BRIDGE_AUTH_TOKEN",
        }:
            monkeypatch.delenv(key, raising=False)

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CCMINI_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return home


def test_load_config_applies_remote_overlay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)

    sync_remote_config({"provider": "mock", "model": "remote-model"})

    cfg = load_config()

    assert cfg.provider == "mock"
    assert cfg.model == "remote-model"


def test_load_config_reads_ccmini_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("CCMINI_PROVIDER", "mock")
    monkeypatch.setenv("CCMINI_MAX_TURNS", "77")
    monkeypatch.setenv("CCMINI_PROMPT_SUGGESTION_ENABLED", "false")
    monkeypatch.setenv("CCMINI_SPECULATION_ENABLED", "false")

    cfg = load_config()

    assert cfg.provider == "mock"
    assert cfg.max_turns == 77
    assert cfg.prompt_suggestion_enabled is False
    assert cfg.speculation_enabled is False


def test_send_message_bridge_reads_ccmini_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    seen: list[object] = []

    class DummyBridgeSession:
        def __init__(self, config: object) -> None:
            seen.append(config)

        async def start(self) -> None:
            return None

        async def query(self, _message: str) -> None:
            return None

        async def stop(self) -> None:
            return None

    monkeypatch.setenv("CCMINI_BRIDGE_BASE_URL", "http://127.0.0.1:7779")
    monkeypatch.setenv("CCMINI_BRIDGE_AUTH_TOKEN", "secret")
    monkeypatch.setattr(send_message_module, "RemoteBridgeSession", DummyBridgeSession)

    tool = SendMessageTool(_DummyRunner())
    result = asyncio.run(tool.execute(context=_ToolContext(), to="bridge:abc", message="hi"))

    assert result.startswith("Message sent to bridge session 'abc'.")
    assert len(seen) == 1
    assert getattr(seen[0], "base_url") == "http://127.0.0.1:7779"
    assert getattr(seen[0], "auth_token") == "secret"


def test_agent_tool_background_launch_returns_task_id_alias() -> None:
    class _BackgroundRunner:
        def spawn(self, **kwargs: object) -> str:
            self.kwargs = kwargs
            return "agent-123"

        def get_status(self, task_id: str) -> object:
            assert task_id == "agent-123"
            return SimpleNamespace(output_file="D:/tmp/task_outputs/agent-123.md")

    parent_agent = SimpleNamespace(
        background_runner=_BackgroundRunner(),
        _coordinator_mode=None,
        _team_create_tool=None,
    )
    tool = AgentTool(provider=_DummyProvider())
    context = ToolUseContext(
        conversation_id="conv-1",
        agent_id="agent-1",
        messages=[],
        extras={"agent": parent_agent},
    )

    raw = asyncio.run(
        tool.execute(
            context=context,
            name="bg-worker",
            description="background smoke",
            prompt="Reply with exactly CORE_BACKGROUND_OK",
            role="worker",
            run_in_background=True,
        )
    )
    payload = json.loads(raw)

    assert payload["status"] == "async_launched"
    assert payload["agentId"] == "agent-123"
    assert payload["task_id"] == "agent-123"
    assert payload["outputFile"].endswith("agent-123.md")


def test_builtin_background_agent_launches_async_by_default() -> None:
    class _BackgroundRunner:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def spawn(self, **kwargs: object) -> str:
            self.kwargs = kwargs
            return "agent-verification"

        def get_status(self, task_id: str) -> object:
            assert task_id == "agent-verification"
            return SimpleNamespace(output_file="D:/tmp/task_outputs/agent-verification.md")

    parent_agent = SimpleNamespace(
        background_runner=_BackgroundRunner(),
        _coordinator_mode=None,
        _team_create_tool=None,
    )
    tool = AgentTool(provider=_DummyProvider())
    context = ToolUseContext(
        conversation_id="conv-1",
        agent_id="agent-1",
        messages=[],
        extras={"agent": parent_agent},
    )

    raw = asyncio.run(
        tool.execute(
            context=context,
            description="verification sweep",
            prompt="Verify the previous implementation and report a verdict.",
            subagent_type="verification",
        )
    )
    payload = json.loads(raw)

    assert payload["status"] == "async_launched"
    assert payload["agentId"] == "agent-verification"
    assert parent_agent.background_runner.kwargs["max_turns"] == 15
    assert "verification specialist" in str(parent_agent.background_runner.kwargs["system_prompt"]).lower()


def test_agent_tool_coordinator_mode_launches_async_by_default() -> None:
    class _BackgroundRunner:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def spawn(self, **kwargs: object) -> str:
            self.kwargs = kwargs
            return "agent-coordinator"

        def get_status(self, task_id: str) -> object:
            assert task_id == "agent-coordinator"
            return SimpleNamespace(output_file="D:/tmp/task_outputs/agent-coordinator.md")

    parent_agent = SimpleNamespace(
        background_runner=_BackgroundRunner(),
        _coordinator_mode=SimpleNamespace(is_active=True),
        _team_create_tool=None,
    )
    tool = AgentTool(provider=_DummyProvider())
    context = ToolUseContext(
        conversation_id="conv-1",
        agent_id="agent-1",
        messages=[],
        extras={"agent": parent_agent},
    )

    raw = asyncio.run(
        tool.execute(
            context=context,
            description="coordinator research",
            prompt="Inspect the auth module and report findings.",
            role="worker",
        )
    )
    payload = json.loads(raw)

    assert payload["status"] == "async_launched"
    assert payload["agentId"] == "agent-coordinator"


def test_agent_tool_kairos_mode_launches_async_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BackgroundRunner:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def spawn(self, **kwargs: object) -> str:
            self.kwargs = kwargs
            return "agent-kairos"

        def get_status(self, task_id: str) -> object:
            assert task_id == "agent-kairos"
            return SimpleNamespace(output_file="D:/tmp/task_outputs/agent-kairos.md")

    monkeypatch.setattr(
        AgentTool,
        "_assistant_force_async",
        staticmethod(lambda _parent_agent: True),
    )
    parent_agent = SimpleNamespace(
        background_runner=_BackgroundRunner(),
        _coordinator_mode=None,
        _team_create_tool=None,
    )
    tool = AgentTool(provider=_DummyProvider())
    context = ToolUseContext(
        conversation_id="conv-1",
        agent_id="agent-1",
        messages=[],
        extras={"agent": parent_agent},
    )

    raw = asyncio.run(
        tool.execute(
            context=context,
            description="kairos follow-up",
            prompt="Inspect recent activity and continue the investigation.",
            role="worker",
        )
    )
    payload = json.loads(raw)

    assert payload["status"] == "async_launched"
    assert payload["agentId"] == "agent-kairos"


def test_agent_tool_sync_subagent_uses_background_runner_and_returns_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BackgroundRunner:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}
            self.status_calls = 0
            self.discarded: list[str] = []

        def spawn(self, **kwargs: object) -> str:
            self.kwargs = kwargs
            return "agent-sync"

        def get_status(self, task_id: str) -> object:
            assert task_id == "agent-sync"
            self.status_calls += 1
            if self.status_calls < 2:
                return SimpleNamespace(
                    output_file="D:/tmp/task_outputs/agent-sync.md",
                    status=SimpleNamespace(value="running"),
                    result="",
                    error="",
                )
            return SimpleNamespace(
                output_file="D:/tmp/task_outputs/agent-sync.md",
                status=SimpleNamespace(value="completed"),
                result="SYNC_OK",
                error="",
            )

        def discard_completion(self, task_id: str) -> bool:
            self.discarded.append(task_id)
            return True

    monkeypatch.setattr(
        AgentTool,
        "_assistant_force_async",
        staticmethod(lambda _parent_agent: False),
    )
    monkeypatch.setattr(
        AgentTool,
        "_get_background_hint_threshold_ms",
        staticmethod(lambda: 10_000),
    )
    monkeypatch.setattr(
        AgentTool,
        "_get_auto_background_ms",
        staticmethod(lambda: 0),
    )
    parent_agent = SimpleNamespace(
        background_runner=_BackgroundRunner(),
        _coordinator_mode=None,
        _team_create_tool=None,
    )
    tool = AgentTool(provider=_DummyProvider())
    context = ToolUseContext(
        conversation_id="conv-1",
        agent_id="agent-1",
        messages=[],
        extras={"agent": parent_agent},
    )

    result = asyncio.run(
        tool.execute(
            context=context,
            description="sync review",
            prompt="Read the auth module and report the answer.",
            role="worker",
        )
    )

    assert result == "SYNC_OK"
    assert parent_agent.background_runner.kwargs["task_type"] is TaskType.LOCAL_AGENT
    assert parent_agent.background_runner.discarded == ["agent-sync"]


def test_agent_tool_sync_subagent_auto_backgrounds_after_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BackgroundRunner:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def spawn(self, **kwargs: object) -> str:
            self.kwargs = kwargs
            return "agent-auto"

        def get_status(self, task_id: str) -> object:
            assert task_id == "agent-auto"
            return SimpleNamespace(
                output_file="D:/tmp/task_outputs/agent-auto.md",
                status=SimpleNamespace(value="running"),
                result="",
                error="",
            )

    monkeypatch.setattr(
        AgentTool,
        "_assistant_force_async",
        staticmethod(lambda _parent_agent: False),
    )
    monkeypatch.setattr(
        AgentTool,
        "_get_background_hint_threshold_ms",
        staticmethod(lambda: 10_000),
    )
    monkeypatch.setattr(
        AgentTool,
        "_get_auto_background_ms",
        staticmethod(lambda: 1),
    )
    parent_agent = SimpleNamespace(
        background_runner=_BackgroundRunner(),
        _coordinator_mode=None,
        _team_create_tool=None,
    )
    tool = AgentTool(provider=_DummyProvider())
    context = ToolUseContext(
        conversation_id="conv-1",
        agent_id="agent-1",
        messages=[],
        extras={"agent": parent_agent},
    )

    raw = asyncio.run(
        tool.execute(
            context=context,
            description="auto background review",
            prompt="Keep digging and continue in the background if needed.",
            role="worker",
        )
    )
    payload = json.loads(raw)

    assert payload["status"] == "async_launched"
    assert payload["agentId"] == "agent-auto"


def test_in_process_teammate_cannot_spawn_background_agent() -> None:
    class _BackgroundRunner:
        def spawn(self, **kwargs: object) -> str:
            raise AssertionError("background spawn should be blocked")

    parent_agent = SimpleNamespace(
        background_runner=_BackgroundRunner(),
        _coordinator_mode=None,
        _team_create_tool=SimpleNamespace(_active_team_name="team-alpha"),
    )
    tool = AgentTool(provider=_DummyProvider())
    context = ToolUseContext(
        conversation_id="conv-1",
        agent_id="reviewer@team-alpha",
        messages=[],
        extras={"agent": parent_agent},
    )

    result = asyncio.run(
        tool.execute(
            context=context,
            description="background follow-up",
            prompt="Keep investigating.",
            role="worker",
            run_in_background=True,
        )
    )

    assert "In-process teammates cannot spawn background agents" in result


def test_in_process_teammate_cannot_spawn_builtin_background_agent() -> None:
    class _BackgroundRunner:
        def spawn(self, **kwargs: object) -> str:
            raise AssertionError("background spawn should be blocked")

    parent_agent = SimpleNamespace(
        background_runner=_BackgroundRunner(),
        _coordinator_mode=None,
        _team_create_tool=SimpleNamespace(_active_team_name="team-alpha"),
    )
    tool = AgentTool(provider=_DummyProvider())
    context = ToolUseContext(
        conversation_id="conv-1",
        agent_id="reviewer@team-alpha",
        messages=[],
        extras={"agent": parent_agent},
    )

    result = asyncio.run(
        tool.execute(
            context=context,
            description="verification sweep",
            prompt="Verify the implementation.",
            subagent_type="verification",
        )
    )

    assert "In-process teammates cannot spawn background agents" in result


def test_agent_tool_worker_resolution_uses_coordinator_safe_tools() -> None:
    tool = AgentTool(
        provider=_DummyProvider(),
        parent_tools=[
            _NamedTool("Read"),
            _NamedTool("Bash"),
            _NamedTool("SendMessage"),
            _NamedTool("Agent", aliases=("Task",)),
        ],
    )

    resolved = tool._resolve_tools(WORKER_AGENT, is_worker_context=True)

    assert [candidate.name for candidate in resolved] == ["Read", "Bash"]


def test_agent_tool_remote_isolation_launches_remote_typed_background_task() -> None:
    class _BackgroundRunner:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        def spawn(self, **kwargs: object) -> str:
            self.kwargs = kwargs
            return "remote-123"

        def get_status(self, task_id: str) -> object:
            assert task_id == "remote-123"
            return SimpleNamespace(output_file="/tmp/remote-123.md")

    parent_agent = SimpleNamespace(
        background_runner=_BackgroundRunner(),
        _coordinator_mode=None,
        _team_create_tool=None,
    )
    tool = AgentTool(provider=_DummyProvider())
    context = ToolUseContext(
        conversation_id="conv-1",
        agent_id="agent-1",
        messages=[],
        extras={"agent": parent_agent},
    )

    payload = json.loads(
        asyncio.run(
            tool.execute(
                context=context,
                description="remote worker",
                prompt="Do the remote thing",
                isolation="remote",
            )
        )
    )

    assert payload["status"] == "async_launched"
    assert parent_agent.background_runner.kwargs["task_type"] is TaskType.REMOTE_AGENT
    assert parent_agent.background_runner.kwargs["metadata"]["backendType"] == "embedded-remote"


def test_send_message_accepts_agent_id_alias() -> None:
    runner = _DummyRunner()
    runner.status_by_id["agent-123"] = SimpleNamespace(
        status=SimpleNamespace(value="running"),
        result="",
        error="",
        description="background smoke",
    )
    tool = SendMessageTool(runner)
    result = asyncio.run(
        tool.execute(
            context=_ToolContext(),
            agentId="agent-123",
            message="hi",
            summary="follow up",
        )
    )

    assert "agent-123" in result
    assert runner.sent_messages == [("agent-123", "hi")]


def test_task_stop_accepts_agent_id_alias() -> None:
    runner = _DummyRunner()
    tool = TaskStopTool(background_runner=runner)

    result = asyncio.run(
        tool.execute(
            context=ToolUseContext(conversation_id="conv-1"),
            agentId="agent-456",
        )
    )

    assert result == "Task agent-456 stop signal sent."
    assert runner.cancelled == ["agent-456"]


def test_openai_complete_falls_back_to_streaming_when_content_is_null() -> None:
    class FakeStream:
        def __init__(self, chunks: list[object]) -> None:
            self._chunks = chunks
            self._index = 0

        def __aiter__(self) -> "FakeStream":
            return self

        async def __anext__(self) -> object:
            if self._index >= len(self._chunks):
                raise StopAsyncIteration
            chunk = self._chunks[self._index]
            self._index += 1
            return chunk

    class FakeCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def create(self, **kwargs: object) -> object:
            self.calls.append(dict(kwargs))
            if kwargs.get("stream"):
                return FakeStream(
                    [
                        SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    delta=SimpleNamespace(content="FALLBACK", tool_calls=None),
                                )
                            ]
                        ),
                        SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    delta=SimpleNamespace(content="_OK", tool_calls=None),
                                )
                            ]
                        ),
                        SimpleNamespace(choices=[]),
                    ]
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=None, tool_calls=None),
                    )
                ]
            )

    fake_completions = FakeCompletions()
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=fake_completions))
    provider = OpenAICompatibleProvider(
        ProviderConfig(
            type="compatible",
            model="gpt-5.4",
            api_key="test-key",
            base_url="https://example.invalid/v1",
            max_tokens=32,
            temperature=0,
        )
    )
    provider._client = fake_client

    message = asyncio.run(
        provider.complete(
            messages=[user_message("Return the required token.")],
            system="Reply exactly with FALLBACK_OK.",
            max_tokens=16,
            temperature=0,
            query_source="test_fallback",
        )
    )

    assert message.text == "FALLBACK_OK"
    assert len(fake_completions.calls) == 2
    assert fake_completions.calls[1]["stream"] is True


def test_load_profile_accepts_empty_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = _prepare_environment(monkeypatch, tmp_path)
    profiles_dir = home / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / "empty.json").write_text("{}", encoding="utf-8")

    cfg = load_profile("empty")

    assert isinstance(cfg, CLIConfig)


def test_load_config_ignores_legacy_project_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    (tmp_path / ".mini-agent.json").write_text(
        json.dumps({"provider": "mock"}),
        encoding="utf-8",
    )

    cfg = load_config()

    assert cfg.provider != "mock"


def test_load_auth_state_skips_invalid_timestamps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = _prepare_environment(monkeypatch, tmp_path)
    (home / "auth.json").write_text(
        json.dumps(
            {
                "providers": {
                    "broken": {"api_key": "bad", "updated_at": "not-a-float"},
                    "good": {"api_key": "ok", "updated_at": 123.0},
                }
            }
        ),
        encoding="utf-8",
    )

    state = load_auth_state()

    assert "broken" not in state
    assert state["good"].api_key == "ok"


def test_build_ready_payload_rewrites_wildcard_bind_host() -> None:
    payload = _build_ready_payload(
        BridgeConfig(enabled=True, host="0.0.0.0", port=7779, auth_token="token"),
    )

    assert payload["serverUrl"] == "http://127.0.0.1:7779"
    assert payload["authToken"] == "token"


def test_remote_executor_handle_uses_connectable_urls() -> None:
    async def run() -> tuple[str, str]:
        host = RemoteExecutorHost(
            agent_factory=lambda _sid: _DummyAgent(),
            bridge_config=BridgeConfig(enabled=True, host="0.0.0.0", port=9999, auth_token="tok"),
        )
        handle = await host.create_session()
        await host._shutdown_session_async(handle.session_id)
        return handle.base_url, handle.websocket_url

    base_url, websocket_url = asyncio.run(run())

    assert base_url == "http://127.0.0.1:9999"
    assert websocket_url == "ws://127.0.0.1:9999"


def test_remote_executor_host_forwards_runtime_prompt_events() -> None:
    created: dict[str, _DummyAgent] = {}

    def factory(session_id: str) -> _DummyAgent:
        agent = _DummyAgent()
        created[session_id] = agent
        return agent

    async def run() -> list[dict[str, object]]:
        host = RemoteExecutorHost(
            agent_factory=factory,
            bridge_config=BridgeConfig(enabled=True, host="127.0.0.1", port=9999, auth_token="tok"),
        )
        handle = await host.create_session()
        agent = created[handle.session_id]
        assert len(agent._listeners) == 1
        maybe = agent._listeners[0](PromptSuggestionEvent(text="run the tests", shown_at=1.0))
        if asyncio.iscoroutine(maybe):
            await maybe
        events = host.api.get_events(handle.session_id, since=0, limit=10) or []
        await host._shutdown_session_async(handle.session_id)
        return events

    events = asyncio.run(run())

    assert any(
        event["payload"].get("event_type") == "prompt_suggestion"
        and event["payload"].get("text") == "run the tests"
        for event in events
    )


def test_bridge_tasks_endpoint_lists_session_task_board(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    server = BridgeServer(BridgeConfig(enabled=True, host="127.0.0.1", port=9999, auth_token="tok"))
    board = TaskBoard()
    board.set_scope("session-abc")
    created = board.create(subject="Plan rollout", description="Break down the rollout", active_form="Planning rollout")
    board.update(created.id, status="in_progress", owner="agent-1")

    class _Request:
        headers = {"Authorization": "Bearer tok"}
        query = {"session_id": "session-abc", "include_completed": "true"}

    response = asyncio.run(server._aiohttp_tasks(_Request()))
    payload = json.loads(response.text)

    assert payload["task_list_id"] == "session-abc"
    assert payload["tasks"][0]["subject"] == "Plan rollout"
    assert payload["tasks"][0]["status"] == "in_progress"
    assert payload["tasks"][0]["owner"] == "agent-1"


def test_bridge_tasks_endpoint_reports_owner_activity_from_team_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    write_team_file(
        "session-abc",
        {
            "name": "session-abc",
            "members": [
                {"agentId": "agent-1", "name": "researcher", "isActive": False},
            ],
        },
    )
    server = BridgeServer(BridgeConfig(enabled=True, host="127.0.0.1", port=9999, auth_token="tok"))
    board = TaskBoard()
    board.set_scope("session-abc")
    created = board.create(subject="Review diff", description="Check the latest changes")
    board.update(created.id, status="in_progress", owner="agent-1")

    class _Request:
        headers = {"Authorization": "Bearer tok"}
        query = {"session_id": "session-abc", "include_completed": "true"}

    response = asyncio.run(server._aiohttp_tasks(_Request()))
    payload = json.loads(response.text)

    assert payload["tasks"][0]["ownerIsActive"] is False


def test_bridge_tasks_endpoint_reports_session_scoped_plan_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)

    class _PlanAgent(_DummyAgent):
        def __init__(self, *, active: bool) -> None:
            super().__init__()
            if active:
                self._messages = [
                    assistant_message(
                        [ToolUseBlock(id="plan-enter", name="EnterPlanMode", input={})]
                    ),
                ]
            else:
                self._messages = [
                    assistant_message(
                        [ToolUseBlock(id="plan-exit", name="ExitPlanMode", input={})]
                    ),
                    Message(
                        role="user",
                        content=[
                            ToolResultBlock(
                                tool_use_id="plan-exit",
                                content="## Implementation Plan\n\n- Review tasks",
                            )
                        ],
                        metadata={
                            "toolUseResult": "## Implementation Plan\n\n- Review tasks",
                        },
                    ),
                ]

    async def run() -> tuple[dict[str, object], dict[str, object]]:
        active_by_call = iter([True, False])
        host = RemoteExecutorHost(
            agent_factory=lambda _session_id: _PlanAgent(active=next(active_by_call)),
            bridge_config=BridgeConfig(enabled=True, host="127.0.0.1", port=9999, auth_token="tok"),
        )
        active_handle = await host.create_session(metadata={"source": "ccmini-frontend"})
        ready_handle = await host.create_session(metadata={"source": "ccmini-frontend"})

        class _ActiveRequest:
            headers = {"Authorization": "Bearer tok"}
            query = {"session_id": active_handle.session_id, "include_completed": "true"}

        class _ReadyRequest:
            headers = {"Authorization": "Bearer tok"}
            query = {"session_id": ready_handle.session_id, "include_completed": "true"}

        active_response = await host.server._aiohttp_tasks(_ActiveRequest())
        ready_response = await host.server._aiohttp_tasks(_ReadyRequest())
        await host._shutdown_session_async(active_handle.session_id)
        await host._shutdown_session_async(ready_handle.session_id)
        return json.loads(active_response.text), json.loads(ready_response.text)

    active_payload, ready_payload = asyncio.run(run())

    assert active_payload["planState"]["isActive"] is True
    assert active_payload["planState"]["planText"] == ""
    assert ready_payload["planState"]["isActive"] is False
    assert ready_payload["planState"]["planText"] == "## Implementation Plan\n\n- Review tasks"


def test_bridge_tasks_endpoint_includes_background_and_team_snapshots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    write_team_file(
        "team-alpha",
        {
            "name": "team-alpha",
            "description": "alpha crew",
            "members": [
                {
                    "agentId": "team-lead@team-alpha",
                    "name": "team-lead",
                    "isActive": True,
                    "backendType": "in-process",
                }
            ],
        },
    )

    class _SnapshotRunner:
        def list_task_snapshots(self, *, include_completed: bool = True) -> list[dict[str, object]]:
            assert include_completed is True
            return [
                {
                    "id": "a123",
                    "type": "local_agent",
                    "status": "running",
                    "description": "Review diff",
                    "outputFile": "/tmp/a123.md",
                    "transcriptFile": "/tmp/a123.jsonl",
                    "workerName": "reviewer",
                    "backendType": "in-process",
                    "isolation": "shared",
                    "canResume": True,
                }
            ]

    class _SnapshotTeam:
        def list_teammates(self) -> list[TeammateState]:
            return [
                TeammateState(
                    identity=TeammateIdentity(
                        agent_id="reviewer@team-alpha",
                        agent_name="reviewer",
                        team_name="team-alpha",
                    ),
                    status=TeammateStatus.RUNNING,
                    current_task="Check tests",
                    is_idle=False,
                    last_update_ms=123,
                    transcript_file="/tmp/reviewer.jsonl",
                )
            ]

    class _SnapshotTeamTool:
        _active_team_name = "team-alpha"

        def get_team(self, team_name: str) -> object | None:
            assert team_name == "team-alpha"
            return _SnapshotTeam()

    class _SnapshotAgent(_DummyAgent):
        def __init__(self) -> None:
            super().__init__()
            self.background_runner = _SnapshotRunner()
            self._team_create_tool = _SnapshotTeamTool()

    async def run() -> dict[str, object]:
        host = RemoteExecutorHost(
            agent_factory=lambda _sid: _SnapshotAgent(),
            bridge_config=BridgeConfig(enabled=True, host="127.0.0.1", port=9999, auth_token="tok"),
        )
        handle = await host.create_session(metadata={"source": "ccmini-frontend"})

        class _Request:
            headers = {"Authorization": "Bearer tok"}
            query = {"session_id": handle.session_id, "include_completed": "true"}

        response = await host.server._aiohttp_tasks(_Request())
        payload = json.loads(response.text)
        await host._shutdown_session_async(handle.session_id)
        return payload

    payload = asyncio.run(run())

    assert payload["backgroundTasks"][0]["id"] == "a123"
    assert payload["team"]["name"] == "team-alpha"
    assert payload["team"]["members"][1]["name"] == "reviewer"


def test_bridge_tasks_endpoint_uses_runtime_task_list_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    board = TaskBoard()
    board.set_scope("team-alpha")
    board.create(subject="Shared task", description="Team-wide task")

    class _TeamTool:
        _active_team_name = "team-alpha"

        def get_team(self, team_name: str) -> object | None:
            assert team_name == "team-alpha"
            return None

    class _TaskListAgent(_DummyAgent):
        def __init__(self) -> None:
            super().__init__()
            self._team_create_tool = _TeamTool()

    async def run() -> dict[str, object]:
        host = RemoteExecutorHost(
            agent_factory=lambda _sid: _TaskListAgent(),
            bridge_config=BridgeConfig(enabled=True, host="127.0.0.1", port=9999, auth_token="tok"),
        )
        handle = await host.create_session(metadata={"source": "ccmini-frontend"})

        class _Request:
            headers = {"Authorization": "Bearer tok"}
            query = {"session_id": handle.session_id, "include_completed": "true"}

        response = await host.server._aiohttp_tasks(_Request())
        await host._shutdown_session_async(handle.session_id)
        return json.loads(response.text)

    payload = asyncio.run(run())

    assert payload["task_list_id"] == "team-alpha"
    assert payload["tasks"][0]["subject"] == "Shared task"


def test_bridge_runtime_task_control_and_transcript(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    transcript_path = tmp_path / "task-transcript.jsonl"
    transcript_path.write_text(
        json.dumps({"role": "assistant", "content": "done"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    class _ControlAgent(_DummyAgent):
        def __init__(self) -> None:
            super().__init__()
            self.cancelled: list[str] = []
            self.sent: list[tuple[str, str]] = []

        def get_task(self, task_id: str) -> object | None:
            if task_id != "a123":
                return None
            return SimpleNamespace(
                id="a123",
                status=SimpleNamespace(value="completed"),
                description="remote task",
                type=SimpleNamespace(value="remote_agent"),
                output_file="/tmp/a123.md",
                transcript_file=str(transcript_path),
                metadata={},
            )

        def cancel_task(self, task_id: str) -> bool:
            self.cancelled.append(task_id)
            return True

        def send_message_to_task(self, task_id: str, message: str) -> bool:
            self.sent.append((task_id, message))
            return True

    async def run() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        host = RemoteExecutorHost(
            agent_factory=lambda _sid: _ControlAgent(),
            bridge_config=BridgeConfig(enabled=True, host="127.0.0.1", port=9999, auth_token="tok"),
        )
        handle = await host.create_session(metadata={"source": "ccmini-frontend"})

        stop_result = await host.api.control_runtime_task(
            handle.session_id,
            task_id="a123",
            action="stop",
            payload={},
        )
        send_result = await host.api.control_runtime_task(
            handle.session_id,
            task_id="a123",
            action="send_message",
            payload={"message": "follow up"},
        )
        transcript_result = host.api.get_runtime_transcript(
            handle.session_id,
            task_id="a123",
            limit=10,
        )
        await host._shutdown_session_async(handle.session_id)
        return stop_result, send_result, transcript_result

    stop_result, send_result, transcript_result = asyncio.run(run())

    assert stop_result["ok"] is True
    assert send_result["ok"] is True
    assert transcript_result["ok"] is True
    assert transcript_result["entries"][0]["content"] == "done"


def test_bridge_task_control_resets_completed_task_list(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    board = TaskBoard()
    board.set_scope("team-alpha")
    created = board.create(subject="Done task", description="Already finished")
    board.update(created.id, status="completed")

    class _TeamTool:
        _active_team_name = "team-alpha"

        def get_team(self, team_name: str) -> object | None:
            assert team_name == "team-alpha"
            return None

    class _ResetAgent(_DummyAgent):
        def __init__(self) -> None:
            super().__init__()
            self._team_create_tool = _TeamTool()

        def get_task(self, task_id: str) -> object | None:
            del task_id
            return None

    async def run() -> dict[str, object]:
        host = RemoteExecutorHost(
            agent_factory=lambda _sid: _ResetAgent(),
            bridge_config=BridgeConfig(enabled=True, host="127.0.0.1", port=9999, auth_token="tok"),
        )
        handle = await host.create_session(metadata={"source": "ccmini-frontend"})
        result = await host.api.control_runtime_task(
            handle.session_id,
            task_id="",
            action="reset_task_list_if_completed",
            payload={},
        )
        await host._shutdown_session_async(handle.session_id)
        return result

    result = asyncio.run(run())

    assert result["ok"] is True
    assert result["cleared"] is True
    assert result["task_list_id"] == "team-alpha"
    assert board.list() == []


def test_bridge_team_task_control_rejects_unknown_teammate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    write_team_file(
        "team-alpha",
        {
            "name": "team-alpha",
            "members": [
                {"agentId": "known@team-alpha", "name": "known", "isActive": True},
            ],
        },
    )

    class _ControlTeam:
        def __init__(self) -> None:
            self.shutdowns: list[str] = []
            self.sent: list[tuple[str, str]] = []

        async def shutdown_teammate(self, agent_name: str) -> None:
            self.shutdowns.append(agent_name)

        def send_message(self, agent_name: str, text: str) -> bool:
            self.sent.append((agent_name, text))
            return agent_name == "known@team-alpha"

    class _ControlTeamTool:
        _active_team_name = "team-alpha"

        def __init__(self) -> None:
            self.team = _ControlTeam()

        def get_team(self, team_name: str) -> object | None:
            assert team_name == "team-alpha"
            return self.team

    class _ControlAgent(_DummyAgent):
        def __init__(self) -> None:
            super().__init__()
            self._team_create_tool = _ControlTeamTool()

        def get_task(self, task_id: str) -> object | None:
            del task_id
            return None

    async def run() -> tuple[dict[str, object], dict[str, object], _ControlAgent]:
        agent = _ControlAgent()
        host = RemoteExecutorHost(
            agent_factory=lambda _sid: agent,
            bridge_config=BridgeConfig(enabled=True, host="127.0.0.1", port=9999, auth_token="tok"),
        )
        handle = await host.create_session(metadata={"source": "ccmini-frontend"})
        stop_result = await host.api.control_runtime_task(
            handle.session_id,
            task_id="ghost@team-alpha",
            action="stop",
            payload={},
        )
        send_result = await host.api.control_runtime_task(
            handle.session_id,
            task_id="ghost@team-alpha",
            action="send_message",
            payload={"message": "follow up"},
        )
        await host._shutdown_session_async(handle.session_id)
        return stop_result, send_result, agent

    stop_result, send_result, agent = asyncio.run(run())

    assert stop_result["ok"] is False
    assert send_result["ok"] is False
    assert agent._team_create_tool.team.shutdowns == []
    assert agent._team_create_tool.team.sent == []


def test_persistent_teammate_syncs_claim_and_completion_to_task_board(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    board = TaskBoard(path=get_task_board_path("team-alpha"))
    record = board.create(subject="Review auth", description="Inspect auth flow")
    task_list = SharedTaskList()
    task_list.upsert(
        task_id=record.id,
        subject=record.subject,
        description=record.description,
        status="pending",
        owner="",
        blocked_by=[],
    )
    teammate = PersistentTeammate(
        config=SimpleNamespace(
            name="reviewer",
            team_name="team-alpha",
            initial_prompt="Bootstrap",
            system_prompt="You are a helpful assistant.",
            tools=[],
            provider=_DummyProvider(),
            max_turns_per_prompt=3,
            color=None,
            model="",
            working_directory="",
            plan_mode_required=False,
            transcript_file="",
        ),
        mailbox=MemoryMailbox(),
        provider=_DummyProvider(),
        task_list=task_list,
    )
    seen_prompts: list[str] = []

    async def _fake_execute(prompt: str) -> SimpleNamespace:
        seen_prompts.append(prompt)
        if prompt.startswith(f"Complete task #{record.id}:"):
            teammate.shutdown()
        return SimpleNamespace(success=True, reply="done", error="")

    monkeypatch.setattr(teammate, "_execute_prompt", _fake_execute)

    asyncio.run(teammate.run())

    updated = board.get(record.id)
    assert updated is not None
    assert updated.status == "completed"
    assert updated.owner == "reviewer"
    assert any(prompt.startswith(f"Complete task #{record.id}:") for prompt in seen_prompts)
    assert task_list.list_all()[0]["status"] == "completed"


def test_team_shutdown_teammate_unassigns_owned_tasks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    board = TaskBoard(path=get_task_board_path("team-alpha"))
    record = board.create(subject="Need follow-up", description="Still in progress")
    board.update(record.id, status="in_progress", owner="reviewer")

    task_list = SharedTaskList()
    task_list.upsert(
        task_id=record.id,
        subject=record.subject,
        description=record.description,
        status="in_progress",
        owner="reviewer",
        blocked_by=[],
    )
    team = Team(
        provider=_DummyProvider(),
        config=TeamConfig(team_name="team-alpha"),
        mailbox=MemoryMailbox(),
        task_list=task_list,
    )

    class _StubTeammate:
        def __init__(self) -> None:
            self.identity = TeammateIdentity(
                agent_id="reviewer@team-alpha",
                agent_name="reviewer",
                team_name="team-alpha",
            )
            self.shutdown_calls = 0

        def shutdown(self) -> None:
            self.shutdown_calls += 1

    stub = _StubTeammate()
    team._teammates[stub.identity.agent_id] = stub  # type: ignore[assignment]

    asyncio.run(team.shutdown_teammate("reviewer"))

    updated = board.get(record.id)
    assert updated is not None
    assert updated.status == "pending"
    assert updated.owner is None
    assert task_list.list_all()[0]["status"] == "pending"
    assert task_list.list_all()[0]["owner"] == ""
    assert stub.shutdown_calls == 1


def test_run_subagent_writes_transcript_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)

    async def fake_query(_params: object) -> object:
        yield CompletionEvent(text="SUBAGENT_OK")

    monkeypatch.setattr(subagent_module, "query", fake_query)

    reply = asyncio.run(
        subagent_module.run_subagent(
            provider=_DummyProvider(),
            system_prompt="You are helpful.",
            user_text="hello",
            tools=[],
            agent_id="unit-subagent",
        )
    )

    transcript_path = Path(subagent_module._subagent_transcript_path("unit-subagent", "subagent"))
    assert reply == "SUBAGENT_OK"
    assert transcript_path.exists()
    transcript_text = transcript_path.read_text(encoding="utf-8")
    assert '"content": "hello"' in transcript_text
    assert '"content": "SUBAGENT_OK"' in transcript_text


def test_background_runner_records_transcript_and_resume_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)

    async def fake_run_subagent(**kwargs: object) -> str:
        prompt = str(kwargs.get("user_text", ""))
        return f"reply:{prompt}"

    monkeypatch.setattr(background_module, "run_subagent", fake_run_subagent)

    async def run() -> tuple[object, str]:
        runner = background_module.BackgroundAgentRunner(provider=_DummyProvider(), task_manager=TaskManager())
        task_id = runner.spawn(
            name="worker-a",
            prompt="first pass",
            metadata={"teamName": "alpha"},
        )
        assert await runner.wait_completion(timeout=0.1) is not None
        for _ in range(10):
            info = runner.get_status(task_id)
            if info is not None and getattr(info.status, "value", info.status) == "completed":
                break
            await asyncio.sleep(0)
        assert runner.send_message(task_id, "follow up") is True
        assert await runner.wait_completion(timeout=0.1) is not None
        info = runner.get_status(task_id)
        assert info is not None
        transcript_text = Path(info.transcript_file).read_text(encoding="utf-8")
        return info, transcript_text

    info, transcript_text = asyncio.run(run())

    assert info.metadata["resumeCount"] == 1
    assert info.transcript_file.endswith(".jsonl")
    assert '"content": "first pass"' in transcript_text
    assert '"content": "follow up"' in transcript_text


def test_create_remote_executor_host_passes_agent_config() -> None:
    seen: dict[str, object] = {}

    def fake_create_agent(**kwargs: object) -> _DummyAgent:
        seen.update(kwargs)
        return _DummyAgent()

    original = factory_module.create_agent
    factory_module.create_agent = fake_create_agent
    try:
        host = create_remote_executor_host(
            provider=object(),
            system_prompt="system",
            config=AgentConfig(max_turns=77),
        )
        agent = host._agent_factory("session-1")
    finally:
        factory_module.create_agent = original

    assert isinstance(agent, _DummyAgent)
    assert isinstance(seen.get("config"), AgentConfig)
    assert seen["config"].max_turns == 77


def test_frontend_host_passes_cli_max_turns_to_remote_executor_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    cfg = CLIConfig(
        provider="mock",
        model="mock-model",
        max_turns=77,
        ccmini_host="127.0.0.1",
        ccmini_port=7779,
    )

    class _ImmediateEvent:
        def set(self) -> None:
            return None

        async def wait(self) -> None:
            return None

    class _FakeHost:
        def __init__(self) -> None:
            self.config = BridgeConfig(enabled=True, host="127.0.0.1", port=7779, auth_token="")

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(
        frontend_host_module,
        "_parse_args",
        lambda: SimpleNamespace(
            host=None,
            port=None,
            auth_token=None,
            provider=None,
            model=None,
            api_key=None,
            base_url=None,
            system_prompt=None,
            profile="coding_assistant",
        ),
    )
    monkeypatch.setattr(frontend_host_module, "load_config", lambda cli_overrides=None: cfg)
    monkeypatch.setattr(frontend_host_module, "_port_is_available", lambda host, port: True)
    monkeypatch.setattr(frontend_host_module.asyncio, "Event", _ImmediateEvent)
    monkeypatch.setattr(frontend_host_module.signal, "signal", lambda *args, **kwargs: None)

    def fake_create_remote_executor_host(**kwargs: object) -> _FakeHost:
        seen.update(kwargs)
        return _FakeHost()

    monkeypatch.setattr(
        frontend_host_module,
        "create_remote_executor_host",
        fake_create_remote_executor_host,
    )

    assert asyncio.run(frontend_host_module._run()) == 0
    assert isinstance(seen.get("config"), AgentConfig)
    assert seen["config"].max_turns == 77


def test_frontend_host_port_probe_supports_ipv6(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class FakeSocket:
        def __init__(self, family: int, socktype: int, proto: int = 0) -> None:
            calls.append(family)
            self._family = family

        def __enter__(self) -> "FakeSocket":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
            return False

        def setsockopt(self, *args: object) -> None:
            return None

        def bind(self, sockaddr: tuple[object, ...]) -> None:
            self._sockaddr = sockaddr

        def getsockname(self) -> tuple[str, int, int, int]:
            return ("::1", 4321, 0, 0)

    def fake_getaddrinfo(
        host: str,
        port: int,
        family: int = 0,
        type: int = 0,
        flags: int = 0,
    ) -> list[tuple[int, int, int, str, tuple[object, ...]]]:
        del family, type, flags
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (host, port, 0, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(socket, "socket", FakeSocket)

    assert _port_is_available("::1", 7779) is True
    assert _find_open_port("::1") == 4321
    assert calls == [socket.AF_INET6, socket.AF_INET6]


def test_load_hook_config_reads_ccmini_project_config(tmp_path: Path) -> None:
    hooks_dir = tmp_path / ".ccmini"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": "echo hi"}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_hook_config(str(tmp_path))

    assert "SessionStart" in config
    assert len(config["SessionStart"]) == 1


def test_team_create_returns_valid_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = _prepare_environment(monkeypatch, tmp_path)
    tool = TeamCreateTool(provider=_DummyProvider())

    payload = asyncio.run(
        tool.execute(
            context=ToolUseContext(conversation_id="conv-1"),
            team_name="windows-team",
            description="smoke",
        )
    )
    data = json.loads(payload)

    assert data["team_name"] == "windows-team"
    assert Path(data["team_file_path"]) == home / "teams" / "windows-team" / "team.json"
    assert data["lead_agent_id"] == "team-lead@windows-team"
    assert data["success"] is True


def test_task_update_persists_status_and_description(tmp_path: Path) -> None:
    board = TaskBoard(path=tmp_path / "task-board")
    create_tool = TaskCreateTool(board)
    get_tool = TaskGetTool(board)
    list_tool = TaskListTool(board)
    update_tool = TaskUpdateTool(board)
    context = ToolUseContext(
        conversation_id="conv-1",
        agent_id="team-lead@coord-team",
        extras={"team_name": "coord-team"},
    )

    created = json.loads(
        asyncio.run(
            create_tool.execute(
                context=context,
                subject="Investigate hooks",
                description="Trace hook execution",
            )
        )
    )
    task_id = created["task"]["id"]

    updated = json.loads(
        asyncio.run(
            update_tool.execute(
                context=context,
                taskId=task_id,
                status="in_progress",
                description="Tracing hook execution deeply",
            )
        )
    )
    fetched = json.loads(asyncio.run(get_tool.execute(context=context, taskId=task_id)))
    listed = json.loads(asyncio.run(list_tool.execute(context=context)))

    assert updated["success"] is True
    assert "status" in updated["updatedFields"]
    assert "description" in updated["updatedFields"]
    assert updated["statusChange"] == {"from": "pending", "to": "in_progress"}
    assert fetched["task"]["status"] == "in_progress"
    assert fetched["task"]["description"] == "Tracing hook execution deeply"
    assert listed["tasks"][0]["status"] == "in_progress"


def test_task_output_accepts_agent_id_alias(tmp_path: Path) -> None:
    output_file = tmp_path / "agent-789.md"
    output_file.write_text("background output", encoding="utf-8")
    runner = _DummyRunner()
    runner.status_by_id["agent-789"] = SimpleNamespace(
        id="agent-789",
        status=SimpleNamespace(value="completed"),
        description="background smoke",
        result="background output",
        error="",
        output_file=str(output_file),
    )
    tool = TaskOutputTool(runner)

    payload = json.loads(
        asyncio.run(
            tool.execute(
                context=ToolUseContext(conversation_id="conv-1"),
                agentId="agent-789",
            )
        )
    )

    assert payload["retrieval_status"] == "success"
    assert payload["task"]["task_id"] == "agent-789"
    assert payload["task"]["output"] == "background output"


def test_check_directory_trust_accepts_ccmini_marker(tmp_path: Path) -> None:
    trusted_dir = tmp_path / ".ccmini"
    trusted_dir.mkdir(parents=True, exist_ok=True)
    (trusted_dir / "trusted").write_text("", encoding="utf-8")

    assert check_directory_trust(tmp_path) is True


def test_list_peers_uses_ccmini_home_when_legacy_env_is_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = _prepare_environment(monkeypatch, tmp_path)
    legacy_home = tmp_path / "legacy-home"
    legacy_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MINI_AGENT_HOME", str(legacy_home))

    assert _get_sessions_dir() == home / "sessions"


def test_conversation_recovery_uses_ccmini_home_when_legacy_env_is_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = _prepare_environment(monkeypatch, tmp_path)
    legacy_home = tmp_path / "legacy-home"
    legacy_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MINI_AGENT_HOME", str(legacy_home))

    recovery = ConversationRecovery(agent_id="demo")

    assert recovery._dir == home / "recovery"


def test_kairos_dream_uses_auto_dream_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    dream_module._dream_tasks.clear()
    seen: dict[str, object] = {}

    async def fake_run_consolidation(
        provider: object,
        *,
        memory_dir: str = "",
        project_root: str = "",
        session_dir: str = "",
        current_session: str = "",
        task: object | None = None,
    ) -> list[str]:
        seen.update(
            {
                "provider": provider,
                "memory_dir": memory_dir,
                "project_root": project_root,
                "session_dir": session_dir,
                "current_session": current_session,
            }
        )
        assert task is not None
        task.status = auto_dream_module.DreamTaskStatus.RUNNING
        task.current_phase = "Consolidate"
        task.add_turn("consolidating memories")
        await asyncio.sleep(0)
        task.complete()
        return [str(Path(memory_dir) / "topic.md")]

    monkeypatch.setattr(auto_dream_module, "run_consolidation", fake_run_consolidation)

    task_id = asyncio.run(
        dream_module.run_nightly_dream(
            object(),
            memory_dir=tmp_path / "memory",
            current_session="conv-1",
            task_id="dream-1",
        )
    )
    task = dream_module.get_dream_task(task_id)

    assert task_id == "dream-1"
    assert seen["current_session"] == "conv-1"
    assert Path(str(seen["memory_dir"])) == tmp_path / "memory"
    assert task is not None
    assert task.status == "complete"
    assert task.phase == dream_module.DreamPhase.COMPLETE
    assert any("topic.md" in turn.text for turn in task.turns)


def test_auto_dream_force_flag_reads_ccmini_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("CCMINI_FORCE_DREAM", "true")

    assert auto_dream_module.is_forced() is True


def test_auto_dream_consolidation_agent_can_inspect_memories_and_transcripts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")
    (memory_dir / "topic.md").write_text("Build still flaky.\n", encoding="utf-8")
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "s1.jsonl").write_text(
        '{"role":"assistant","content":"build failed on windows"}\n',
        encoding="utf-8",
    )
    auto_dream_module.reset_auto_dream_state()

    async def fake_run_forked_agent(**kwargs: object) -> object:
        context = kwargs["context"]
        tools = list(kwargs["tools"])
        assert context.can_use_tool("memory_inspect") is True
        assert context.can_use_tool("memory_action") is True
        assert context.can_use_tool("Bash") is False

        inspect_tool = next(tool for tool in tools if tool.name == "memory_inspect")
        action_tool = next(tool for tool in tools if tool.name == "memory_action")
        tool_context = ToolUseContext(conversation_id="conv-1")

        listed = await inspect_tool.execute(
            context=tool_context,
            action="list",
            scope="memory",
        )
        read_topic = await inspect_tool.execute(
            context=tool_context,
            action="read",
            scope="memory",
            path="topic.md",
        )
        transcript_match = await inspect_tool.execute(
            context=tool_context,
            action="grep",
            scope="transcripts",
            pattern="build failed",
        )
        denied = await inspect_tool.execute(
            context=tool_context,
            action="read",
            scope="memory",
            path="../outside.md",
        )
        write_result = await action_tool.execute(
            context=tool_context,
            action="update",
            path="topic.md",
            content="Build fixed on April 7, 2026.\n",
        )

        assert "FILE MEMORY.md" in listed
        assert "Build still flaky." in read_topic
        assert "s1.jsonl:1:" in transcript_match
        assert denied.startswith("Error:")
        assert "topic.md" in write_result
        return SimpleNamespace(text="Consolidated memories after inspection.")

    async def fake_scan_memory_files(memory_root: str) -> list[dict[str, str]]:
        assert Path(memory_root) == memory_dir
        return [{"path": "topic.md", "title": "Topic", "summary": "Build still flaky."}]

    monkeypatch.setattr(
        auto_dream_module,
        "scan_memory_files",
        fake_scan_memory_files,
    )
    monkeypatch.setattr(
        auto_dream_module,
        "_list_sessions_since",
        lambda *args, **kwargs: [
            auto_dream_module.SessionInfo(
                session_id="s1",
                path=str(session_dir / "s1.jsonl"),
                mtime=1.0,
                message_count=1,
            )
        ],
    )
    monkeypatch.setattr(
        auto_dream_module,
        "format_memory_manifest",
        lambda existing: "topic.md - Build still flaky.",
    )
    monkeypatch.setattr(subagent_module, "run_forked_agent", fake_run_forked_agent)

    touched = asyncio.run(
        auto_dream_module.run_consolidation(
            _DummyProvider(),
            memory_dir=str(memory_dir),
            session_dir=str(session_dir),
        )
    )

    assert touched == [str(memory_dir / "topic.md")]
    assert (memory_dir / "topic.md").read_text(encoding="utf-8") == "Build fixed on April 7, 2026.\n"


def test_extract_memories_agent_can_inspect_existing_memories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")
    (memory_dir / "prefs.md").write_text("User prefers pytest -v.\n", encoding="utf-8")
    extract_memories_module.reset_extract_state()

    async def fake_run_forked_agent(**kwargs: object) -> object:
        context = kwargs["context"]
        tools = list(kwargs["tools"])
        assert context.can_use_tool("memory_inspect") is True
        assert context.can_use_tool("memory_write") is True
        assert context.can_use_tool("Bash") is False

        inspect_tool = next(tool for tool in tools if tool.name == "memory_inspect")
        write_tool = next(tool for tool in tools if tool.name == "memory_write")
        tool_context = ToolUseContext(conversation_id="conv-1")

        listed = await inspect_tool.execute(context=tool_context, action="list")
        read_back = await inspect_tool.execute(
            context=tool_context,
            action="read",
            path="prefs.md",
        )
        grep_back = await inspect_tool.execute(
            context=tool_context,
            action="grep",
            pattern="pytest",
        )
        denied = await inspect_tool.execute(
            context=tool_context,
            action="read",
            path="../outside.md",
        )
        write_result = await write_tool.execute(
            context=tool_context,
            path="prefs.md",
            content="User prefers pytest -vv.\n",
        )

        assert "FILE MEMORY.md" in listed
        assert "User prefers pytest -v." in read_back
        assert "prefs.md:1:" in grep_back
        assert denied.startswith("Error:")
        assert "prefs.md" in write_result
        return SimpleNamespace(text="Extracted durable memories.")

    async def fake_scan_memory_files(memory_root: str) -> list[dict[str, str]]:
        assert Path(memory_root) == memory_dir
        return [{"path": "prefs.md", "title": "Prefs", "summary": "User prefers pytest -v."}]

    monkeypatch.setattr(extract_memories_module, "scan_memory_files", fake_scan_memory_files)
    monkeypatch.setattr(
        extract_memories_module,
        "format_memory_manifest",
        lambda existing: "prefs.md - User prefers pytest -v.",
    )
    monkeypatch.setattr(subagent_module, "run_forked_agent", fake_run_forked_agent)

    written = asyncio.run(
        extract_memories_module.extract_memories(
            [user_message("remember I prefer pytest -vv"), assistant_message("Will do.")],
            _DummyProvider(),
            memory_dir=str(memory_dir),
        )
    )

    assert written == [str(memory_dir / "prefs.md")]
    assert (memory_dir / "prefs.md").read_text(encoding="utf-8") == "User prefers pytest -vv.\n"


def test_proactive_suggestion_engine_does_not_dispatch_other_subsystems() -> None:
    engine = ProactiveSuggestionEngine()
    events: list[tuple[str, ...]] = []

    async def run() -> None:
        async def on_suggest(kind: str, text: str) -> None:
            events.append(("suggest", kind, text))

        async def on_away() -> None:
            events.append(("away",))

        async def on_dream() -> None:
            events.append(("dream",))

        engine.set_suggest_callback(on_suggest)
        engine.set_away_summary_callback(on_away)
        engine.set_dream_trigger_callback(on_dream)

        await engine.evaluate(IdleLevel.ACTIVE)
        await engine.evaluate(IdleLevel.IDLE)
        await engine.evaluate(IdleLevel.IDLE)
        await engine.evaluate(IdleLevel.AWAY)
        await engine.evaluate(IdleLevel.AWAY)
        await engine.evaluate(IdleLevel.ACTIVE)
        await engine.evaluate(IdleLevel.IDLE)

    asyncio.run(run())

    assert events == []


def test_prompt_suggestion_hook_uses_guarded_v2_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_generate_v2(
        messages: list[object],
        provider: object,
        config: object,
        *,
        app_state: object | None = None,
        previous_suggestion: str = "",
        **kwargs: object,
    ) -> str:
        seen["messages"] = messages
        seen["provider"] = provider
        seen["config"] = config
        seen["app_state"] = app_state
        seen["previous_suggestion"] = previous_suggestion
        seen["extra_kwargs"] = kwargs
        return "run the tests"

    def fail_simple_path(*args: object, **kwargs: object) -> None:
        raise AssertionError("generate_suggestion should not be used")

    prompt_suggestion_module._current_suggestion = prompt_suggestion_module.PromptSuggestionState(
        text="commit this",
        generated_at=1.0,
    )
    monkeypatch.setattr(prompt_suggestion_module, "generate_suggestion_v2", fake_generate_v2)
    monkeypatch.setattr(prompt_suggestion_module, "generate_suggestion", fail_simple_path)
    monkeypatch.setattr(prompt_suggestion_module, "start_speculation", lambda *args, **kwargs: None)

    hook = prompt_suggestion_module.PromptSuggestionHook(_DummyProvider())
    context = PostSamplingContext(
        messages=[
            user_message("fix the bug"),
            assistant_message(
                "Patched the runtime.",
                usage={
                    "input_tokens": 321,
                    "cache_creation_tokens": 45,
                    "output_tokens": 67,
                },
            ),
        ],
        system_prompt="system",
        reply_text="Patched the runtime.",
        query_source="repl_main_thread",
    )
    agent = SimpleNamespace(
        background_runner=SimpleNamespace(list_active=lambda: [SimpleNamespace(id="bg-1")]),
        _pending_client_run_id="run-1",
        _last_user_activity_at=123.0,
        _runtime_is_non_interactive=False,
    )

    asyncio.run(hook.on_post_sampling(context, agent=agent))

    assert prompt_suggestion_module.get_current_suggestion().text == "run the tests"
    assert seen["previous_suggestion"] == "commit this"
    app_state = seen["app_state"]
    assert app_state is not None
    assert app_state.pending_worker_task is True
    assert app_state.elicitation_in_progress is True
    assert app_state.user_last_typed_at == 123.0
    assert seen["extra_kwargs"]["parent_input_tokens"] == 321
    assert seen["extra_kwargs"]["parent_cache_write_tokens"] == 45
    assert seen["extra_kwargs"]["parent_output_tokens"] == 67


def test_prompt_suggestion_hook_starts_speculation_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def fake_generate_v2(*args: object, **kwargs: object) -> str:
        return "run the tests"

    def fake_start_speculation(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(prompt_suggestion_module, "generate_suggestion_v2", fake_generate_v2)
    monkeypatch.setattr(prompt_suggestion_module, "start_speculation", fake_start_speculation)

    hook = prompt_suggestion_module.PromptSuggestionHook(
        _DummyProvider(),
        config=prompt_suggestion_module.PromptSuggestionConfig(
            enabled=True,
            speculation_enabled=True,
        ),
    )
    context = PostSamplingContext(
        messages=[user_message("fix the bug"), assistant_message("done")],
        system_prompt="system",
        reply_text="done",
        query_source="repl_main_thread",
    )
    agent = SimpleNamespace(
        background_runner=SimpleNamespace(list_active=lambda: []),
        _pending_client_run_id=None,
        _last_user_activity_at=0.0,
        _runtime_is_non_interactive=False,
    )

    asyncio.run(hook.on_post_sampling(context, agent=agent))

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[3] == "run the tests"
    assert kwargs["agent"] is agent


def test_prompt_suggestion_hook_skips_sdk_noninteractive_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_generate_v2(*args: object, **kwargs: object) -> str:
        nonlocal called
        called = True
        return "should not happen"

    monkeypatch.setattr(prompt_suggestion_module, "generate_suggestion_v2", fake_generate_v2)

    hook = prompt_suggestion_module.PromptSuggestionHook(_DummyProvider())
    context = PostSamplingContext(
        messages=[
            user_message("fix the bug"),
            assistant_message("Patched the runtime."),
        ],
        system_prompt="system",
        reply_text="Patched the runtime.",
        query_source="sdk",
    )
    agent = SimpleNamespace(_runtime_is_non_interactive=True)

    asyncio.run(hook.on_post_sampling(context, agent=agent))

    assert called is False


def test_agent_installs_prompt_suggestion_hook_with_runtime_toggles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("CCMINI_PROMPT_SUGGESTION_ENABLED", "false")
    monkeypatch.setenv("CCMINI_SPECULATION_ENABLED", "false")

    agent = Agent(
        provider=ProviderConfig(type="mock", model="mock-model"),
        system_prompt="You are helpful.",
    )
    hook = next(
        runtime_hook
        for runtime_hook in agent._hooks
        if isinstance(runtime_hook, prompt_suggestion_module.PromptSuggestionHook)
    )

    assert hook._config.enabled is False
    assert hook._config.speculation_enabled is False


def test_start_speculation_uses_forked_agent_and_emits_ready_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_events: list[object] = []

    async def fake_run_forked_agent(**kwargs: object) -> object:
        assert kwargs["query_source"] == "prompt_suggestion_speculation"
        assert kwargs["fork_label"] == "speculation"
        return subagent_module.ForkedAgentResult(
            text="prefetched reply",
            tool_results=[],
            messages_added=1,
            aborted=False,
            usage={},
            duration_ms=12.0,
            stop_reason="end_turn",
            events=[
                TextEvent(text="prefetched "),
                CompletionEvent(text="prefetched reply", stop_reason="end_turn"),
            ],
            added_messages=[assistant_message("prefetched reply")],
        )

    agent = SimpleNamespace(
        _tools=[],
        _working_directory="D:/work/py/reachy_mini/src/ccmini",
        _event_queue=asyncio.Queue(),
        _fire_event=lambda event: seen_events.append(event),
    )
    monkeypatch.setattr(subagent_module, "run_forked_agent", fake_run_forked_agent)

    async def run() -> None:
        prompt_suggestion_module.start_speculation(
            [user_message("fix the bug"), assistant_message("done")],
            "system",
            _DummyProvider(),
            "run the tests",
            agent=agent,
        )
        assert prompt_suggestion_module._current_speculation_task is not None
        await prompt_suggestion_module._current_speculation_task

    asyncio.run(run())

    state = prompt_suggestion_module.get_current_speculation(agent)
    assert state.status == "ready"
    assert state.reply == "prefetched reply"
    assert len(state.added_messages) == 1
    assert state.added_messages[0].role == "assistant"
    assert any(
        isinstance(event, SpeculationEvent) and event.status == "running"
        for event in seen_events
    )
    assert any(
        isinstance(event, SpeculationEvent) and event.status == "ready"
        for event in seen_events
    )


def test_speculation_overlay_keeps_workspace_unchanged_until_accept(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "note.txt"
    target.write_text("original\n", encoding="utf-8")

    agent = SimpleNamespace(
        _tools=[FileReadTool(), FileWriteTool()],
        _working_directory=str(workspace),
        _permission_checker=PermissionChecker(
            PermissionConfig(mode=PermissionMode.ACCEPT_EDITS)
        ),
    )
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    written_paths: set[str] = set()
    tools = prompt_suggestion_module._make_speculation_tools(
        agent,
        workspace_root=str(workspace),
        overlay_dir=str(overlay_dir),
        written_paths=written_paths,
    )
    tool_map = {tool.name: tool for tool in tools}
    context = ToolUseContext(
        conversation_id="conv-1",
        agent_id="agent-1",
        extras={"working_directory": str(workspace)},
        abort_event=asyncio.Event(),
    )

    async def run() -> tuple[str, str]:
        write_result = await tool_map["Write"].execute(
            context=context,
            file_path="note.txt",
            content="overlay\n",
        )
        read_result = await tool_map["Read"].execute(
            context=context,
            file_path="note.txt",
        )
        return write_result, read_result

    write_result, read_result = asyncio.run(run())

    assert "Successfully wrote" in write_result
    assert target.read_text(encoding="utf-8") == "original\n"
    assert "overlay" in read_result
    assert "original" not in read_result
    assert written_paths == {"note.txt"}

    state = prompt_suggestion_module.SpeculationState(
        overlay_dir=str(overlay_dir),
        workspace_root=str(workspace),
        written_paths=["note.txt"],
    )
    prompt_suggestion_module._commit_speculation_overlay(state)
    assert target.read_text(encoding="utf-8") == "overlay\n"
    prompt_suggestion_module._cleanup_speculation_overlay(state)
    assert not overlay_dir.exists()


def test_speculation_write_pauses_without_auto_accept_edits(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "note.txt"
    target.write_text("original\n", encoding="utf-8")

    agent = SimpleNamespace(
        _tools=[FileWriteTool()],
        _working_directory=str(workspace),
        _permission_checker=PermissionChecker(
            PermissionConfig(mode=PermissionMode.DEFAULT)
        ),
    )
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    wrapper = prompt_suggestion_module._make_speculation_tools(
        agent,
        workspace_root=str(workspace),
        overlay_dir=str(overlay_dir),
        written_paths=set(),
    )[0]
    abort_event = asyncio.Event()

    with pytest.raises(prompt_suggestion_module._SpeculationBoundaryReached):
        asyncio.run(
            wrapper.execute(
                context=ToolUseContext(
                    conversation_id="conv-1",
                    agent_id="agent-1",
                    extras={"working_directory": str(workspace)},
                    abort_event=abort_event,
                ),
                file_path="note.txt",
                content="overlay\n",
            )
        )

    assert target.read_text(encoding="utf-8") == "original\n"
    assert agent._speculation_boundary_tracker["boundary"].type == "edit"


def test_speculation_wrapper_allows_read_only_bash(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    seen: dict[str, object] = {}
    bash = BashTool()

    async def fake_execute(*, context: object, **kwargs: object) -> str:
        seen["context"] = context
        seen["kwargs"] = kwargs
        return "status clean"

    bash.execute = fake_execute  # type: ignore[method-assign]
    agent = SimpleNamespace(
        _tools=[bash],
        _working_directory=str(workspace),
    )
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    wrapper = prompt_suggestion_module._make_speculation_tools(
        agent,
        workspace_root=str(workspace),
        overlay_dir=str(overlay_dir),
        written_paths=set(),
    )[0]

    result = asyncio.run(
        wrapper.execute(
            context=ToolUseContext(
                conversation_id="conv-1",
                agent_id="agent-1",
                extras={"working_directory": str(workspace)},
                abort_event=asyncio.Event(),
            ),
            command="git status",
        )
    )

    assert result == "status clean"
    assert seen["kwargs"] == {"command": "git status"}


def test_speculation_wrapper_allows_safe_chained_bash(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    seen: dict[str, object] = {}
    bash = BashTool()

    async def fake_execute(*, context: object, **kwargs: object) -> str:
        seen["context"] = context
        seen["kwargs"] = kwargs
        return "status and diff"

    bash.execute = fake_execute  # type: ignore[method-assign]
    agent = SimpleNamespace(
        _tools=[bash],
        _working_directory=str(workspace),
    )
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    wrapper = prompt_suggestion_module._make_speculation_tools(
        agent,
        workspace_root=str(workspace),
        overlay_dir=str(overlay_dir),
        written_paths=set(),
    )[0]

    result = asyncio.run(
        wrapper.execute(
            context=ToolUseContext(
                conversation_id="conv-1",
                agent_id="agent-1",
                extras={"working_directory": str(workspace)},
                abort_event=asyncio.Event(),
            ),
            command="git status && git diff --stat",
        )
    )

    assert result == "status and diff"
    assert seen["kwargs"] == {"command": "git status && git diff --stat"}


def test_speculation_wrapper_allows_safe_echo_git_chain(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    seen: dict[str, object] = {}
    bash = BashTool()

    async def fake_execute(*, context: object, **kwargs: object) -> str:
        seen["context"] = context
        seen["kwargs"] = kwargs
        return "header and status"

    bash.execute = fake_execute  # type: ignore[method-assign]
    agent = SimpleNamespace(
        _tools=[bash],
        _working_directory=str(workspace),
    )
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    wrapper = prompt_suggestion_module._make_speculation_tools(
        agent,
        workspace_root=str(workspace),
        overlay_dir=str(overlay_dir),
        written_paths=set(),
    )[0]

    result = asyncio.run(
        wrapper.execute(
            context=ToolUseContext(
                conversation_id="conv-1",
                agent_id="agent-1",
                extras={"working_directory": str(workspace)},
                abort_event=asyncio.Event(),
            ),
            command="echo ready && git status",
        )
    )

    assert result == "header and status"
    assert seen["kwargs"] == {"command": "echo ready && git status"}


def test_speculation_wrapper_allows_safe_powershell(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    seen: dict[str, object] = {}
    import ccmini.tools.powershell as powershell_module

    tool = powershell_module.PowerShellTool()

    async def fake_execute(*, context: object, **kwargs: object) -> str:
        seen["context"] = context
        seen["kwargs"] = kwargs
        return "mode ok"

    tool.execute = fake_execute  # type: ignore[method-assign]
    agent = SimpleNamespace(
        _tools=[tool],
        _working_directory=str(workspace),
    )
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    wrapper = prompt_suggestion_module._make_speculation_tools(
        agent,
        workspace_root=str(workspace),
        overlay_dir=str(overlay_dir),
        written_paths=set(),
    )[0]

    result = asyncio.run(
        wrapper.execute(
            context=ToolUseContext(
                conversation_id="conv-1",
                agent_id="agent-1",
                extras={"working_directory": str(workspace)},
                abort_event=asyncio.Event(),
            ),
            command="Get-ChildItem",
        )
    )

    assert result == "mode ok"
    assert seen["kwargs"] == {"command": "Get-ChildItem"}


def test_speculation_wrapper_allows_navigation_powershell(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    seen: dict[str, object] = {}
    import ccmini.tools.powershell as powershell_module

    tool = powershell_module.PowerShellTool()

    async def fake_execute(*, context: object, **kwargs: object) -> str:
        seen["context"] = context
        seen["kwargs"] = kwargs
        return "navigated"

    tool.execute = fake_execute  # type: ignore[method-assign]
    agent = SimpleNamespace(
        _tools=[tool],
        _working_directory=str(workspace),
    )
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    wrapper = prompt_suggestion_module._make_speculation_tools(
        agent,
        workspace_root=str(workspace),
        overlay_dir=str(overlay_dir),
        written_paths=set(),
    )[0]

    result = asyncio.run(
        wrapper.execute(
            context=ToolUseContext(
                conversation_id="conv-1",
                agent_id="agent-1",
                extras={"working_directory": str(workspace)},
                abort_event=asyncio.Event(),
            ),
            command="Set-Location . | Get-ChildItem",
        )
    )

    assert result == "navigated"
    assert seen["kwargs"] == {"command": "Set-Location . | Get-ChildItem"}


def test_speculation_wrapper_allows_safe_repl_execute(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    seen: dict[str, object] = {}
    from ccmini.tools.repl import REPLTool

    tool = REPLTool()

    async def fake_execute(*, context: object, **kwargs: object) -> str:
        seen["context"] = context
        seen["kwargs"] = kwargs
        return "3"

    tool.execute = fake_execute  # type: ignore[method-assign]
    agent = SimpleNamespace(
        _tools=[tool],
        _working_directory=str(workspace),
    )
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    wrapper = prompt_suggestion_module._make_speculation_tools(
        agent,
        workspace_root=str(workspace),
        overlay_dir=str(overlay_dir),
        written_paths=set(),
    )[0]

    result = asyncio.run(
        wrapper.execute(
            context=ToolUseContext(
                conversation_id="conv-1",
                agent_id="agent-1",
                extras={"working_directory": str(workspace)},
                abort_event=asyncio.Event(),
            ),
            action="execute",
            language="python",
            code="print(1 + 2)",
        )
    )

    assert result == "3"
    assert seen["kwargs"] == {
        "action": "execute",
        "language": "python",
        "code": "print(1 + 2)",
    }


def test_speculation_wrapper_allows_safe_repl_execute_in_session(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    seen: dict[str, object] = {}
    from ccmini.tools.repl import REPLTool

    tool = REPLTool()

    async def fake_execute(*, context: object, **kwargs: object) -> str:
        seen["context"] = context
        seen["kwargs"] = kwargs
        return "42"

    tool.execute = fake_execute  # type: ignore[method-assign]
    agent = SimpleNamespace(
        _tools=[tool],
        _working_directory=str(workspace),
    )
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    wrapper = prompt_suggestion_module._make_speculation_tools(
        agent,
        workspace_root=str(workspace),
        overlay_dir=str(overlay_dir),
        written_paths=set(),
    )[0]

    result = asyncio.run(
        wrapper.execute(
            context=ToolUseContext(
                conversation_id="conv-1",
                agent_id="agent-1",
                extras={"working_directory": str(workspace)},
                abort_event=asyncio.Event(),
            ),
            action="execute_in_session",
            language="python",
            session_id="sess-1",
            code="print(value[0])",
        )
    )

    assert result == "42"
    assert seen["kwargs"] == {
        "action": "execute_in_session",
        "language": "python",
        "session_id": "sess-1",
        "code": "print(value[0])",
    }


def test_speculation_wrapper_blocks_unsafe_repl_execute(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    from ccmini.tools.repl import REPLTool

    tool = REPLTool()
    agent = SimpleNamespace(
        _tools=[tool],
        _working_directory=str(workspace),
    )
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    wrapper = prompt_suggestion_module._make_speculation_tools(
        agent,
        workspace_root=str(workspace),
        overlay_dir=str(overlay_dir),
        written_paths=set(),
    )[0]

    with pytest.raises(prompt_suggestion_module._SpeculationBoundaryReached):
        asyncio.run(
            wrapper.execute(
                context=ToolUseContext(
                    conversation_id="conv-1",
                    agent_id="agent-1",
                    extras={"working_directory": str(workspace)},
                    abort_event=asyncio.Event(),
                ),
                action="execute",
                language="python",
                code="import os\nos.system('whoami')",
            )
        )


def test_bash_command_analyzer_marks_readonly_git_as_safe() -> None:
    risk, reason = BashCommandAnalyzer.classify("git status && git diff --stat")

    assert risk == RiskLevel.SAFE
    assert "readonly" in reason.lower()


def test_bash_command_analyzer_marks_destructive_commands_as_dangerous() -> None:
    risk, reason = BashCommandAnalyzer.classify("rm -rf build")

    assert risk == RiskLevel.DANGEROUS
    assert "destructive" in reason.lower()


def test_bash_command_analyzer_rejects_git_output_flag_as_safe() -> None:
    risk, reason = BashCommandAnalyzer.classify("git diff --output=tmp.txt")

    assert risk == RiskLevel.NEEDS_REVIEW
    assert "allowlist" in reason.lower()


def test_bash_command_analyzer_allows_safe_git_config_query() -> None:
    risk, reason = BashCommandAnalyzer.classify("git config --get user.name")

    assert risk == RiskLevel.SAFE
    assert "readonly" in reason.lower()


def test_bash_command_analyzer_allows_safe_git_remote_verbose() -> None:
    risk, reason = BashCommandAnalyzer.classify("git remote -v")

    assert risk == RiskLevel.SAFE
    assert "readonly" in reason.lower()


def test_bash_command_analyzer_rejects_git_branch_creation_as_safe() -> None:
    risk, reason = BashCommandAnalyzer.classify("git branch feature-x")

    assert risk == RiskLevel.NEEDS_REVIEW
    assert "allowlist" in reason.lower()


def test_bash_command_analyzer_allows_safe_echo() -> None:
    risk, reason = BashCommandAnalyzer.classify("echo ready")

    assert risk == RiskLevel.SAFE
    assert "readonly" in reason.lower()


def test_bash_command_analyzer_rejects_printf_variable_write_flag() -> None:
    risk, reason = BashCommandAnalyzer.classify("printf -v name %s hi")

    assert risk == RiskLevel.NEEDS_REVIEW
    assert "allowlist" in reason.lower()


def test_bash_tool_blocks_dangerous_command_via_analyzer() -> None:
    tool = BashTool()

    result = tool._check_command_safety("rm -rf build")

    assert result is not None
    assert "blocked" in result.lower() or "dangerous" in result.lower()


def test_bash_command_analyzer_blocks_output_redirection() -> None:
    risk, reason = BashCommandAnalyzer.classify("cat notes.txt > out.txt")

    assert risk == RiskLevel.DANGEROUS
    assert "redirection" in reason.lower()


def test_bash_command_analyzer_rejects_sort_output_flag_as_safe() -> None:
    risk, reason = BashCommandAnalyzer.classify("sort -o out.txt input.txt")

    assert risk == RiskLevel.NEEDS_REVIEW
    assert "allowlist" in reason.lower()


def test_query_engine_replays_matching_ready_speculation_without_new_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    agent = Agent(
        provider=ProviderConfig(type="mock", model="mock-model"),
        system_prompt="You are helpful.",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "note.txt"
    target.write_text("original\n", encoding="utf-8")
    overlay_dir = tmp_path / "spec-overlay"
    overlay_target = overlay_dir / "note.txt"
    overlay_target.parent.mkdir(parents=True, exist_ok=True)
    overlay_target.write_text("overlay\n", encoding="utf-8")
    agent._working_directory = str(workspace)

    prompt_suggestion_module._set_prompt_state(
        prompt_suggestion_module.PromptSuggestionState(
            text="run the tests",
            generated_at=1.0,
            shown_at=1.0,
        ),
        agent=agent,
    )
    prompt_suggestion_module._set_speculation_state(
        prompt_suggestion_module.SpeculationState(
            status="ready",
            suggestion="run the tests",
            reply="prefetched reply",
            events=[
                TextEvent(text="prefetched "),
                CompletionEvent(text="prefetched reply", stop_reason="speculation_accept"),
            ],
            added_messages=[assistant_message("prefetched reply")],
            stop_reason="speculation_accept",
            overlay_dir=str(overlay_dir),
            workspace_root=str(workspace),
            written_paths=["note.txt"],
        ),
        agent=agent,
    )

    async def fail_run_query(*args: object, **kwargs: object):
        raise AssertionError("run_query should not execute for accepted speculation")
        yield None

    monkeypatch.setattr(query_engine_module, "run_query", fail_run_query)

    async def run() -> list[object]:
        engine = query_engine_module.QueryEngine(agent)
        return [event async for event in engine.submit_message("run the tests")]

    events = asyncio.run(run())

    assert [type(event).__name__ for event in events] == ["TextEvent", "CompletionEvent"]
    assert isinstance(events[-1], CompletionEvent)
    assert events[-1].text == "prefetched reply"
    assert agent._messages[-2].role == "user"
    assert agent._messages[-1].role == "assistant"
    assert agent._messages[-1].text == "prefetched reply"
    assert target.read_text(encoding="utf-8") == "overlay\n"
    assert prompt_suggestion_module.get_current_suggestion(agent).text == ""
    assert prompt_suggestion_module.get_current_speculation(agent).status == "idle"
    assert not overlay_dir.exists()


def test_query_engine_replays_blocked_speculation_then_continues_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    agent = Agent(
        provider=ProviderConfig(type="mock", model="mock-model"),
        system_prompt="You are helpful.",
    )

    prompt_suggestion_module._set_prompt_state(
        prompt_suggestion_module.PromptSuggestionState(
            text="run the tests",
            generated_at=1.0,
            shown_at=1.0,
        ),
        agent=agent,
    )
    prompt_suggestion_module._set_speculation_state(
        prompt_suggestion_module.SpeculationState(
            status="blocked",
            suggestion="run the tests",
            reply="partial reply",
            events=[
                TextEvent(text="partial "),
                CompletionEvent(text="partial reply", stop_reason="hook_stopped"),
            ],
            added_messages=[assistant_message("partial reply")],
            stop_reason="hook_stopped",
        ),
        agent=agent,
    )

    async def fake_run_query(params: object):
        params.messages.append(assistant_message("final reply"))  # type: ignore[attr-defined]
        yield CompletionEvent(text="final reply", stop_reason="end_turn")

    monkeypatch.setattr(query_engine_module, "run_query", fake_run_query)

    async def run() -> list[object]:
        engine = query_engine_module.QueryEngine(agent)
        return [event async for event in engine.submit_message("run the tests")]

    events = asyncio.run(run())

    assert [type(event).__name__ for event in events] == [
        "TextEvent",
        "CompletionEvent",
        "CompletionEvent",
    ]
    assert isinstance(events[-1], CompletionEvent)
    assert events[-1].text == "final reply"
    assert agent._messages[-3].role == "user"
    assert agent._messages[-2].role == "assistant"
    assert agent._messages[-2].text == "partial reply"
    assert agent._messages[-1].role == "assistant"
    assert agent._messages[-1].text == "final reply"
    assert prompt_suggestion_module.get_current_speculation(agent).status == "idle"


def test_clear_speculation_cleans_overlay_directory(tmp_path: Path) -> None:
    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    (overlay_dir / "temp.txt").write_text("data", encoding="utf-8")
    agent = SimpleNamespace()

    prompt_suggestion_module._set_speculation_state(
        prompt_suggestion_module.SpeculationState(
            status="ready",
            overlay_dir=str(overlay_dir),
        ),
        agent=agent,
    )

    prompt_suggestion_module.clear_speculation(agent)

    assert not overlay_dir.exists()


def test_fire_post_sampling_propagates_query_source() -> None:
    seen: list[str] = []

    class _CaptureHook(PostSamplingHook):
        async def on_post_sampling(
            self,
            context: PostSamplingContext,
            *,
            agent: object,
        ) -> None:
            del agent
            seen.append(context.query_source)

    async def run() -> None:
        query_module._fire_post_sampling(
            HookRunner([_CaptureHook()]),
            [user_message("hello")],
            "system",
            "reply",
            [],
            agent=SimpleNamespace(),
            query_source="repl_main_thread",
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(run())

    assert seen == ["repl_main_thread"]


def test_append_assistant_turn_stores_usage_metadata() -> None:
    messages: list[object] = []

    assistant_msg = query_module._append_assistant_turn(  # type: ignore[attr-defined]
        messages,
        "streamed reply",
        [],
        turn_usage=UsageRecord(
            input_tokens=123,
            output_tokens=45,
            cache_read_tokens=6,
            cache_creation_tokens=7,
            model="mock-model",
        ),
    )

    assert assistant_msg is not None
    assert assistant_msg.metadata["usage"] == {
        "input_tokens": 123,
        "output_tokens": 45,
        "cache_read_tokens": 6,
        "cache_creation_tokens": 7,
        "model": "mock-model",
    }
    assert assistant_msg.metadata["model"] == "mock-model"
    assert messages[-1] is assistant_msg


def test_auto_dream_hook_forwards_session_context_to_consolidation() -> None:
    seen: dict[str, object] = {}
    scheduled: list[asyncio.Task[object]] = []

    async def fake_run_consolidation(
        provider: object,
        *,
        memory_dir: str = "",
        project_root: str = "",
        session_dir: str = "",
        current_session: str = "",
        task: object | None = None,
    ) -> list[str]:
        seen.update(
            {
                "provider": provider,
                "memory_dir": memory_dir,
                "project_root": project_root,
                "session_dir": session_dir,
                "current_session": current_session,
                "task": task,
            }
        )
        return []

    async def run() -> None:
        hook = auto_dream_module.AutoDreamHook(
            _DummyProvider(),
            memory_dir="D:/memory",
            session_dir="D:/sessions",
        )
        context = PostSamplingContext(
            messages=[user_message("hello"), assistant_message("world")],
            system_prompt="system",
            reply_text="world",
            query_source="repl_main_thread",
        )
        agent = SimpleNamespace(conversation_id="conv-123")

        original_ensure_future = auto_dream_module.asyncio.ensure_future
        try:
            auto_dream_module.should_consolidate = lambda *args, **kwargs: True  # type: ignore[assignment]

            def fake_ensure_future(coro: object) -> asyncio.Task[object]:
                task = asyncio.create_task(coro)  # type: ignore[arg-type]
                scheduled.append(task)
                return task

            auto_dream_module.asyncio.ensure_future = fake_ensure_future  # type: ignore[assignment]
            await hook.on_post_sampling(context, agent=agent)
            if scheduled:
                await asyncio.gather(*scheduled)
        finally:
            auto_dream_module.asyncio.ensure_future = original_ensure_future  # type: ignore[assignment]

    original_should_consolidate = auto_dream_module.should_consolidate
    original_run_consolidation = auto_dream_module.run_consolidation
    try:
        auto_dream_module.should_consolidate = lambda *args, **kwargs: True  # type: ignore[assignment]
        auto_dream_module.run_consolidation = fake_run_consolidation  # type: ignore[assignment]
        asyncio.run(run())
    finally:
        auto_dream_module.should_consolidate = original_should_consolidate  # type: ignore[assignment]
        auto_dream_module.run_consolidation = original_run_consolidation  # type: ignore[assignment]

    assert seen["memory_dir"] == "D:/memory"
    assert seen["session_dir"] == "D:/sessions"
    assert seen["current_session"] == "conv-123"


def test_extract_memories_hook_appends_memory_saved_message() -> None:
    scheduled: list[asyncio.Task[object]] = []
    persisted: list[str] = []

    async def fake_extract_memories(
        messages: list[object],
        provider: object,
        *,
        memory_dir: str = "",
        project_root: str = "",
    ) -> list[str]:
        del messages, provider, project_root
        assert memory_dir == "D:/memory"
        return ["D:/memory/prefs.md"]

    async def run() -> None:
        hook = extract_memories_module.ExtractMemoriesHook(
            _DummyProvider(),
            memory_dir="D:/memory",
        )
        agent = SimpleNamespace(
            _messages=[],
            _persist_session_snapshot=lambda: persisted.append("persisted"),
        )
        context = PostSamplingContext(
            messages=[user_message("remember this"), assistant_message("done")],
            system_prompt="system",
            reply_text="done",
            query_source="repl_main_thread",
        )

        original_ensure_future = extract_memories_module.asyncio.ensure_future
        try:
            def fake_ensure_future(coro: object) -> asyncio.Task[object]:
                task = asyncio.create_task(coro)  # type: ignore[arg-type]
                scheduled.append(task)
                return task

            extract_memories_module.asyncio.ensure_future = fake_ensure_future  # type: ignore[assignment]
            await hook.on_post_sampling(context, agent=agent)
            if scheduled:
                await asyncio.gather(*scheduled)
            assert len(agent._messages) == 1
            assert agent._messages[0].role == "system"
            assert agent._messages[0].metadata["subtype"] == "memory_saved"
            assert agent._messages[0].metadata["verb"] == "Saved"
            assert agent._messages[0].metadata["source"] == "extract_memories"
        finally:
            extract_memories_module.asyncio.ensure_future = original_ensure_future  # type: ignore[assignment]

    original = extract_memories_module.extract_memories
    try:
        extract_memories_module._pending = None
        extract_memories_module._in_flight = None
        extract_memories_module.extract_memories = fake_extract_memories  # type: ignore[assignment]
        asyncio.run(run())
    finally:
        extract_memories_module.extract_memories = original  # type: ignore[assignment]

    assert persisted == ["persisted"]


def test_append_memory_saved_to_agent_emits_runtime_event_when_idle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    agent = Agent(
        provider=ProviderConfig(type="mock", model="mock-model"),
        system_prompt="You are helpful.",
    )

    extract_memories_module._append_memory_saved_to_agent(
        agent,
        ["D:/memory/prefs.md"],
    )

    events = agent.drain_events()

    assert len(events) == 1
    assert isinstance(events[0], TextEvent)
    assert events[0].text == "[Memory saved: D:/memory/prefs.md]"
    assert agent._messages[-1].metadata["subtype"] == "memory_saved"


def test_append_memory_saved_to_agent_emits_runtime_event_immediately_during_submit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    agent = Agent(
        provider=ProviderConfig(type="mock", model="mock-model"),
        system_prompt="You are helpful.",
    )
    agent._is_processing = True

    class _PendingSubmit:
        def done(self) -> bool:
            return False

    seen: list[str] = []
    unsubscribe = agent.on_event(lambda event: seen.append(getattr(event, "text", "")))
    try:
        agent._submit_task = _PendingSubmit()  # type: ignore[assignment]
        extract_memories_module._append_memory_saved_to_agent(
            agent,
            ["D:/memory/prefs.md"],
        )
    finally:
        unsubscribe()

    queued = agent.drain_events()

    assert seen == ["[Memory saved: D:/memory/prefs.md]"]
    assert len(queued) == 1
    assert isinstance(queued[0], TextEvent)
    assert queued[0].text == "[Memory saved: D:/memory/prefs.md]"
    assert agent._runtime_notifications == []


def test_auto_dream_hook_appends_improved_memory_saved_message() -> None:
    scheduled: list[asyncio.Task[object]] = []
    persisted: list[str] = []

    async def fake_run_consolidation(
        provider: object,
        *,
        memory_dir: str = "",
        session_dir: str = "",
        current_session: str = "",
        **kwargs: object,
    ) -> list[str]:
        del provider, kwargs
        assert memory_dir == "D:/memory"
        assert session_dir == "D:/sessions"
        assert current_session == "conv-123"
        return ["D:/memory/MEMORY.md"]

    async def run() -> None:
        hook = auto_dream_module.AutoDreamHook(
            _DummyProvider(),
            memory_dir="D:/memory",
            session_dir="D:/sessions",
        )
        agent = SimpleNamespace(
            conversation_id="conv-123",
            _messages=[],
            _persist_session_snapshot=lambda: persisted.append("persisted"),
        )
        context = PostSamplingContext(
            messages=[user_message("hello"), assistant_message("world")],
            system_prompt="system",
            reply_text="world",
            query_source="repl_main_thread",
        )

        original_ensure_future = auto_dream_module.asyncio.ensure_future
        try:
            auto_dream_module.should_consolidate = lambda *args, **kwargs: True  # type: ignore[assignment]

            def fake_ensure_future(coro: object) -> asyncio.Task[object]:
                task = asyncio.create_task(coro)  # type: ignore[arg-type]
                scheduled.append(task)
                return task

            auto_dream_module.asyncio.ensure_future = fake_ensure_future  # type: ignore[assignment]
            await hook.on_post_sampling(context, agent=agent)
            if scheduled:
                await asyncio.gather(*scheduled)
            assert len(agent._messages) == 1
            assert agent._messages[0].metadata["subtype"] == "memory_saved"
            assert agent._messages[0].metadata["verb"] == "Improved"
            assert agent._messages[0].metadata["source"] == "auto_dream"
        finally:
            auto_dream_module.asyncio.ensure_future = original_ensure_future  # type: ignore[assignment]

    original_should_consolidate = auto_dream_module.should_consolidate
    original_run_consolidation = auto_dream_module.run_consolidation
    try:
        auto_dream_module.should_consolidate = lambda *args, **kwargs: True  # type: ignore[assignment]
        auto_dream_module.run_consolidation = fake_run_consolidation  # type: ignore[assignment]
        asyncio.run(run())
    finally:
        auto_dream_module.should_consolidate = original_should_consolidate  # type: ignore[assignment]
        auto_dream_module.run_consolidation = original_run_consolidation  # type: ignore[assignment]

    assert persisted == ["persisted"]


def test_away_summary_notifications_surface_on_return(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    agent = Agent(
        provider=ProviderConfig(type="mock", model="mock-model"),
        system_prompt="You are helpful.",
    )
    agent._messages = [
        user_message("debug the kairos runtime"),
        assistant_message("I traced the proactive wiring."),
    ]

    class _AwayManager:
        def __init__(self) -> None:
            self.mark_calls = 0

        def should_show(self, messages: list[object] | None = None) -> bool:
            assert messages is not None
            assert len(messages) == 2
            return True

        async def generate(
            self,
            messages: list[object],
            *,
            session_memory: str | None = None,
        ) -> str:
            assert len(messages) == 2
            assert session_memory is None
            return "You were tracing Kairos runtime wiring. Next check the away-summary path."

        def mark_activity(self) -> None:
            self.mark_calls += 1

    manager = _AwayManager()
    agent._away_summary_manager = manager

    events = asyncio.run(agent._consume_away_summary_notifications())

    assert [event.text for event in events] == [
        "[While you were away]\nYou were tracing Kairos runtime wiring. Next check the away-summary path."
    ]
    away_message = agent._messages[-1]
    assert away_message.role == "system"
    assert away_message.text == "You were tracing Kairos runtime wiring. Next check the away-summary path."
    assert away_message.metadata["subtype"] == "away_summary"
    assert manager.mark_calls == 1
    assert agent._last_user_activity_at > 0


def test_away_summary_manager_uses_transcript_for_deduping() -> None:
    manager_obj = away_summary_module.AwaySummaryManager(_DummyProvider(), idle_threshold=0.0)
    manager_obj._summary_shown = False  # simulate fresh process after restore
    manager_obj._last_activity = 1.0
    messages = [
        user_message("debug kairos"),
        assistant_message("I traced the runtime."),
        away_message := system_message(
            "While you were away...",
            subtype="away_summary",
        ),
    ]

    assert away_summary_module.has_summary_since_last_user_turn(messages) is True
    assert manager_obj.should_show(messages) is False
    assert away_message.metadata["subtype"] == "away_summary"


def test_task_manager_accepts_lazy_factories_without_constructing_cancelled_coroutines() -> None:
    async def run() -> bool:
        manager = TaskManager()
        created = False

        async def child() -> str:
            nonlocal created
            created = True
            await asyncio.sleep(10)
            return "done"

        task_id = manager.submit(lambda: child(), name="bg-task")
        assert manager.cancel(task_id) is True
        await asyncio.sleep(0)
        await manager.cancel_all()
        return created

    created = asyncio.run(run())

    assert created is False


def test_submit_user_input_returns_turn_id_and_preserves_host_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)

    async def run() -> tuple[Agent, str, str]:
        agent = Agent(
            provider=ProviderConfig(type="mock", model="mock-model"),
            system_prompt="You are helpful.",
        )
        turn_id = agent.submit_user_input(
            "hello host",
            user_id="user-42",
            metadata={"source": "host"},
            attachments=[{"type": "text", "text": "sensor=ok"}],
        )
        reply = await agent.wait_reply(timeout=5.0)
        return agent, turn_id, reply

    agent, turn_id, reply = asyncio.run(run())

    assert turn_id
    assert reply.startswith("[mock] received:")
    user_msg = agent.messages[0]
    assert user_msg.metadata["source"] == "host"
    assert user_msg.metadata["user_id"] == "user-42"
    assert user_msg.metadata["turn_id"] == turn_id
    assert user_msg.text == "hello host\nsensor=ok"


def test_query_events_include_conversation_and_turn_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)

    async def run() -> tuple[Agent, list[object]]:
        agent = Agent(
            provider=ProviderConfig(type="mock", model="mock-model"),
            system_prompt="You are helpful.",
        )
        events = [event async for event in agent.query("check correlation")]
        return agent, events

    agent, events = asyncio.run(run())

    text_event = next(event for event in events if isinstance(event, TextEvent))
    completion_event = next(event for event in events if isinstance(event, CompletionEvent))

    assert text_event.conversation_id == agent.conversation_id
    assert completion_event.conversation_id == agent.conversation_id
    assert text_event.turn_id
    assert completion_event.turn_id == text_event.turn_id


def test_publish_host_event_persists_and_emits_runtime_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    profile_root = tmp_path / "runtime"

    agent = Agent(
        provider=ProviderConfig(type="mock", model="mock-model"),
        system_prompt="You are helpful.",
    )
    agent.set_memory_roots(profile_root=str(profile_root))
    agent.publish_host_event(
        HostEvent(
            conversation_id=agent.conversation_id,
            event_type="sensor_summary",
            text="battery=20%",
            metadata={"source": "front"},
        )
    )

    events = agent.drain_events()

    assert len(events) == 1
    assert isinstance(events[0], TextEvent)
    assert events[0].conversation_id == agent.conversation_id
    assert events[0].text == "battery=20%"
    assert agent.messages[-1].metadata["event_type"] == "sensor_summary"
    assert agent.messages[-1].metadata["source"] == "front"
    assert agent._memory_store is not None
    recent = agent._memory_store.recent_event_records(agent.conversation_id, 1)
    assert recent[0]["event_type"] == "sensor_summary"
    assert recent[0]["text"] == "battery=20%"


def test_submit_tool_results_accepts_host_tool_results_with_metadata_and_attachments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    captured: list[ToolResultBlock] = []

    async def fake_continue(
        self: query_engine_module.QueryEngine,
        run_id: str,
        results: list[ToolResultBlock],
    ) -> object:
        assert run_id == "run-1"
        captured.extend(results)
        yield CompletionEvent(text="done", conversation_id="conv-1")

    monkeypatch.setattr(query_engine_module.QueryEngine, "continue_with_tool_results", fake_continue)

    async def run() -> list[object]:
        agent = Agent(
            provider=ProviderConfig(type="mock", model="mock-model"),
            system_prompt="You are helpful.",
        )
        return [
            event
            async for event in agent.submit_tool_results(
                "run-1",
                [
                    HostToolResult(
                        tool_use_id="tool-1",
                        text="primary",
                        metadata={"source": "host"},
                        attachments=[{"type": "text", "text": "details"}],
                    )
                ],
            )
        ]

    events = asyncio.run(run())

    assert isinstance(events[-1], CompletionEvent)
    assert len(captured) == 1
    assert captured[0].tool_use_id == "tool-1"
    assert captured[0].metadata["source"] == "host"
    assert isinstance(captured[0].content, list)
    assert any(
        isinstance(block, dict) and block.get("text") == "details"
        for block in captured[0].content
    )


def test_set_mode_and_memory_roots_and_factory_disable_default_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    agent = Agent(
        provider=ProviderConfig(type="mock", model="mock-model"),
        system_prompt="You are helpful.",
    )
    agent.set_working_directory(str(workspace))
    agent.set_mode("coordinator")

    assert agent.get_mode() == "coordinator"
    assert os.environ.get("CLAUDE_CODE_COORDINATOR_MODE", "") == ""
    assert agent.working_directory == str(workspace.resolve())

    roots = tmp_path / "profile"
    agent.set_memory_roots(profile_root=str(roots))
    assert agent._session_store is not None
    assert str(agent._session_store.session_dir).startswith(str(roots))
    assert agent._memory_store is not None
    assert str(agent._memory_store.memory_root).startswith(str(roots))

    created = factory_module.create_agent(
        provider=ProviderConfig(type="mock", model="mock-model"),
        system_prompt="You are helpful.",
        use_default_tools=False,
    )
    assert created.tools == []


def test_agent_kairos_host_api_controls_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)

    async def run() -> tuple[bool, dict[str, object], dict[str, object]]:
        agent = Agent(
            provider=ProviderConfig(type="mock", model="mock-model"),
            system_prompt="You are helpful.",
        )
        cfg = agent.configure_kairos(
            {
                "kairos_enabled": True,
                "brief_enabled": True,
                "proactive_enabled": True,
                "cron_enabled": True,
                "channels_enabled": True,
            }
        )
        assert cfg.kairos_enabled is True

        activated = await agent.activate_kairos(mode="assistant", trust_accepted=True)
        assert activated is True
        assert agent.is_kairos_active() is True

        agent.pause_proactive()
        paused_state = agent.get_kairos_state()
        assert paused_state["paused"] is True

        agent.resume_proactive()
        assert agent.get_kairos_state()["paused"] is False

        assert agent.set_brief_level("minimal") == "minimal"
        assert agent.get_brief_level() == "minimal"
        assert agent.set_view_mode("chat") == "chat"
        assert agent.get_view_mode() == "chat"

        agent.enqueue_runtime_command(
            source="system",
            content="queued task",
            metadata={"origin": "test"},
        )
        await agent.wake("system", "wake ping", {"kind": "wake"})

        accepted = await agent.publish_channel_notification(
            "server:alerts",
            "disk nearly full",
            sender="ops",
        )

        cron_task = agent.create_cron_task(
            name="night-check",
            cron_expr="0 * * * *",
            prompt="run nightly check",
        )
        listed = agent.list_cron_tasks()
        assert any(task.id == cron_task.id for task in listed)
        assert agent.delete_cron_task(cron_task.id) is True

        inbox = agent.get_inbox_snapshot()
        state_before_stop = agent.get_kairos_state()

        await agent.deactivate_kairos()
        assert agent.is_kairos_active() is False
        return accepted, inbox, state_before_stop

    accepted, inbox, state_before_stop = asyncio.run(run())

    assert accepted is True
    assert isinstance(inbox, dict)
    assert state_before_stop["active"] is True
    assert state_before_stop["command_queue_size"] >= 2


def test_agent_buddy_host_api_controls_companion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    seen: list[dict[str, object]] = []

    agent = Agent(
        provider=ProviderConfig(type="mock", model="mock-model"),
        system_prompt="You are helpful.",
    )
    unsubscribe = agent.on_companion_event(lambda event: seen.append(dict(event)))
    try:
        agent.enable_buddy(True)
        assert agent.is_buddy_enabled() is True

        companion = agent.hatch_companion(name="Momo")
        assert companion.name == "Momo"
        assert agent.get_companion() is not None

        intro = agent.get_companion_intro_attachment()
        assert intro
        assert intro[0]["name"] == "Momo"

        before = agent.get_companion_nurture_stats()["pet_count"]
        pet_stats = agent.pet_companion()
        assert pet_stats["pet_count"] == before + 1

        payload = agent.get_companion_render_payload(columns=120)
        assert payload["companion"]["name"] == "Momo"
        assert payload["rendered"]
        assert payload["reserved_columns"] >= 0

        agent.set_companion_muted(True)
        assert agent.is_companion_muted() is True
        assert agent.get_companion_intro_attachment() == []

        agent.enable_buddy(False)
        assert agent.is_buddy_enabled() is False
    finally:
        unsubscribe()

    event_types = [event["event_type"] for event in seen]
    assert "buddy_enabled" in event_types
    assert "companion_hatched" in event_types
    assert "companion_pet" in event_types
    assert "companion_muted" in event_types


def test_agent_team_peer_and_tool_profile_host_api(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)

    from ccmini.tools.list_peers import register_session, unregister_session

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    async def run() -> tuple[list[object], dict[str, object], str]:
        agent = Agent(
            provider=ProviderConfig(type="mock", model="mock-model"),
            system_prompt="You are helpful.",
        )
        agent.set_working_directory(str(workspace))
        agent.set_tools([FileReadTool()])
        agent.set_sub_agent_tools([FileWriteTool()])
        agent.set_tool_profiles(
            {
                "writer": ToolProfile(
                    tools=[FileWriteTool()],
                    system_prompt="write things",
                    max_turns=3,
                )
            }
        )
        assert [tool.name for tool in agent.tools] == ["Read"]
        assert [tool.name for tool in agent.sub_agent_tools] == ["Write"]
        assert list(agent.tool_profiles.keys()) == ["writer"]

        agent.set_mode("coordinator")
        assert agent.is_coordinator_mode() is True
        assert os.environ.get("CLAUDE_CODE_COORDINATOR_MODE", "") == ""

        register_session(
            "peer-alpha",
            name="alpha",
            working_dir=str(workspace),
            model="mock-model",
            agent_id="peer-agent",
        )
        try:
            peers = agent.list_live_peers()
            created = await agent.create_team(
                team_name="host-team",
                description="team from host api",
            )
            deleted = await agent.delete_team("host-team")
            return peers, created, deleted
        finally:
            unregister_session("peer-alpha")

    peers, created, deleted = asyncio.run(run())

    assert any(getattr(peer, "session_id", "") == "peer-alpha" for peer in peers)
    assert created["success"] is True
    assert created["team_name"] == "host-team"
    assert "host-team" in deleted


def test_query_engine_prompt_command_applies_model_effort_and_allowed_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    seen: dict[str, object] = {}

    async def fake_run_query(params: object):
        seen["model"] = params.provider.model_name  # type: ignore[attr-defined]
        seen["tools"] = [tool.name for tool in params.tools]  # type: ignore[attr-defined]
        config = getattr(params.provider, "_config", None)  # type: ignore[attr-defined]
        seen["effort"] = getattr(config, "extras", {}).get("reasoning_effort", "")
        params.messages.append(assistant_message("done"))  # type: ignore[attr-defined]
        yield CompletionEvent(text="done", stop_reason="end_turn")

    monkeypatch.setattr(query_engine_module, "run_query", fake_run_query)

    async def run() -> tuple[list[object], Agent]:
        agent = Agent(
            provider=ProviderConfig(type="mock", model="mock-model"),
            system_prompt="You are helpful.",
            config=AgentConfig(enable_builtin_commands=True),
        )
        agent._tools = [FileReadTool()]
        agent._current_turn_tools = [FileReadTool()]
        agent._command_registry.register_command(
            Command(
                name="focus",
                description="Focused prompt command",
                type=CommandType.PROMPT,
                source=CommandSource.BUILTIN,
                loaded_from=CommandSource.BUILTIN,
                prompt_text="Focus on the requested task.",
                allowed_tools=["Read"],
                model="override-model",
                effort="high",
            )
        )
        engine = query_engine_module.QueryEngine(agent)
        events = [event async for event in engine.submit_message("/focus inspect the repo")]
        return events, agent

    events, agent = asyncio.run(run())

    assert isinstance(events[-1], CompletionEvent)
    assert seen["model"] == "override-model"
    assert seen["effort"] == "high"
    assert seen["tools"] == ["Read"]
    assert "Focus on the requested task." in agent.messages[0].text


def test_query_engine_blocks_unsafe_bridge_slash_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)

    class UnsafeCommand(SlashCommand):
        @property
        def name(self) -> str:
            return "unsafe"

        async def execute(self, args: str, agent: Agent) -> str:
            raise AssertionError(f"unsafe command should not execute: {args} {agent}")

    async def run() -> list[object]:
        agent = Agent(
            provider=ProviderConfig(type="mock", model="mock-model"),
            system_prompt="You are helpful.",
            config=AgentConfig(enable_builtin_commands=True),
        )
        agent._command_registry.register(UnsafeCommand())
        engine = query_engine_module.QueryEngine(agent)
        return [
            event
            async for event in engine.submit_message(
                "/unsafe now",
                metadata={"bridge_origin": True, "skip_slash_commands": True},
            )
        ]

    events = asyncio.run(run())

    assert isinstance(events[-1], CompletionEvent)
    assert "/unsafe isn't available over Remote Control." in events[-1].text


def test_query_engine_user_prompt_submit_hook_can_block_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)

    class FakeHookRunner:
        def __init__(self, cwd: str | None = None, session_id: str = "") -> None:
            self.cwd = cwd
            self.session_id = session_id

        def has_hooks(self, event: object) -> bool:
            return True

        async def fire(self, event: object, hook_input: object) -> object:
            del event, hook_input
            return SimpleNamespace(
                blocking=True,
                should_continue=False,
                system_message="Blocked by UserPromptSubmit",
                reason="",
                stop_reason="",
                additional_context="",
                updated_input=None,
                initial_user_message="",
            )

    monkeypatch.setattr(query_engine_module, "UserHookRunner", FakeHookRunner)

    async def run() -> list[object]:
        agent = Agent(
            provider=ProviderConfig(type="mock", model="mock-model"),
            system_prompt="You are helpful.",
        )
        engine = query_engine_module.QueryEngine(agent)
        return [event async for event in engine.submit_message("hello")]

    events = asyncio.run(run())

    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert events[0].error == "Blocked by UserPromptSubmit"


def test_query_engine_bash_mode_rewrites_prompt_and_filters_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    seen: dict[str, object] = {}

    async def fake_run_query(params: object):
        seen["tools"] = [tool.name for tool in params.tools]  # type: ignore[attr-defined]
        params.messages.append(assistant_message("done"))  # type: ignore[attr-defined]
        yield CompletionEvent(text="done", stop_reason="end_turn")

    monkeypatch.setattr(query_engine_module, "run_query", fake_run_query)

    async def run() -> tuple[list[object], Agent]:
        agent = Agent(
            provider=ProviderConfig(type="mock", model="mock-model"),
            system_prompt="You are helpful.",
        )
        agent._tools = [BashTool(), FileReadTool()]
        agent._current_turn_tools = [BashTool(), FileReadTool()]
        engine = query_engine_module.QueryEngine(agent)
        events = [event async for event in engine.submit_message("!pwd")]
        return events, agent

    events, agent = asyncio.run(run())

    assert isinstance(events[-1], CompletionEvent)
    assert "Shell request:\npwd" in agent.messages[0].text
    assert "Bash" in seen["tools"]
    assert "Read" in seen["tools"]


def test_submit_user_input_image_attachment_adds_metadata_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)
    image_path = tmp_path / "pixel.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    async def run() -> Agent:
        agent = Agent(
            provider=ProviderConfig(type="mock", model="mock-model"),
            system_prompt="You are helpful.",
        )
        turn_id = agent.submit_user_input(
            "inspect image",
            attachments=[{"type": "image", "path": str(image_path)}],
        )
        assert turn_id
        await agent.wait_reply(timeout=5.0)
        return agent

    agent = asyncio.run(run())

    user_content = agent.messages[0].content
    assert isinstance(user_content, list)
    assert any(isinstance(block, TextBlock) and "Image metadata:" in block.text for block in user_content)
    assert any(isinstance(block, ImageBlock) for block in user_content)


def test_ultraplan_keyword_routes_to_ultraplan_prompt_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_environment(monkeypatch, tmp_path)

    async def fake_run_query(params: object):
        params.messages.append(assistant_message("done"))  # type: ignore[attr-defined]
        yield CompletionEvent(text="done", stop_reason="end_turn")

    monkeypatch.setattr(query_engine_module, "run_query", fake_run_query)

    async def run() -> Agent:
        agent = Agent(
            provider=ProviderConfig(type="mock", model="mock-model"),
            system_prompt="You are helpful.",
            config=AgentConfig(enable_builtin_commands=True),
        )
        engine = query_engine_module.QueryEngine(agent)
        _ = [event async for event in engine.submit_message("please ultraplan this migration")]
        return agent

    agent = asyncio.run(run())

    assert "Create a comprehensive execution plan for the user's request." in agent.messages[0].text
