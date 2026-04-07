"""Agent: the top-level entry point for mini-agent.

Supports two interaction modes:
- **CLI mode**: Agent handles its own I/O (stdin/stdout)
- **Embedded resident mode**: Host app creates an Agent, calls start(),
  feeds messages via query(), and handles I/O itself

The main Agent supports **dual execution modes** for each query:

- **Blocking** (``query``): ``async for event in agent.query("Hi"): ...``
  The caller awaits each event as it streams.

- **Non-blocking** (``submit`` / ``poll_events``):
  ``agent.submit("Hi")`` returns immediately, the query runs in the
  background, and ``agent.poll_events()`` / ``agent.drain_events()``
  lets the caller fetch events at its own pace.  This is critical for
  real-time hosts (robot control loops, game loops) that cannot block.

Extended features (ported from Claude Code patterns):
- **Conversation recovery** — save/restore state across crashes
- **Message re-injection** — re-inject important context after compaction
- **Agent summary** — periodic 3-5 word progress summaries
- **Graceful shutdown** — drain pending work, save recovery state
- **Event bus** — decouple subsystem coordination via typed events
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

from .attachments import (
    AttachmentCollector,
    MemoryAttachmentSource,
    ensure_companion_intro_source,
)
from .delegation.background import BackgroundAgentRunner, BackgroundResult
from .commands import SlashCommandRegistry
from .commands.builtin import register_builtin_commands, register_bundled_skills_as_commands
from .embedded import HostEvent, HostToolResult
from .kairos import (
    GateConfig,
    activate_kairos,
    check_directory_trust,
    deactivate_kairos,
    get_assistant_system_prompt_addendum,
    get_brief_system_prompt,
    get_channels_system_prompt,
    get_proactive_system_prompt,
    is_kairos_active,
    set_gate_config,
)
from .providers.fallback import FallbackConfig
from .plugins import PluginRegistry, discover_plugin_dirs_for_path
from .hooks.runner import HookRunner
from .hooks import Hook, IdleAction, IdleHook, OnStreamEventHook, PostQueryHook, PreQueryHook
from .messages import (
    CompletionEvent,
    DocumentBlock,
    ErrorEvent,
    ImageBlock,
    Message,
    StreamEvent,
    TextBlock,
    TextEvent,
    ToolCallEvent,
    ToolResultBlock,
    normalize_tool_result_content,
    system_message,
    user_message,
)
from .delegation.tasks import TaskManager
from .paths import mini_agent_home
from .session.store import _dict_to_message
from .token_budget import BudgetStatus, TokenBudget
from .usage import UsageTracker
from .ux import ScheduledTask, TaskScheduler
from .session.store import SessionStore
from .memory import ConsolidationAgent, JsonlMemoryStore, MemoryAdapter
from .services import (
    AwaySummaryManager,
    AutoDreamHook,
    ExtractMemoriesHook,
    MagicDocsHook,
    PromptSuggestionConfig,
    PromptSuggestionHook,
    SessionMemoryHook,
    get_memory_dir,
)
from .services.stats import StatsTracker
from .services.prevent_sleep import start_prevent_sleep, stop_prevent_sleep
from .prompts import SystemPrompt
from .providers import BaseProvider, ProviderConfig, create_provider
from .engine.compact import CompactTracker, CompactConfig
from .engine.history_snip import SnipConfig
from .engine.context_collapse import CollapseConfig
from .engine.query_engine import QueryEngine
from .tool import Tool, ToolUseContext
from .permissions import PermissionChecker


@dataclass
class ToolProfile:
    """A named set of tools + prompt for sub-agents.

    Use tool profiles to give different sub-agents different capabilities::

        profiles = {
            "researcher": ToolProfile(
                tools=[WebFetchTool(), GrepTool()],
                system_prompt="You are a research specialist.",
            ),
            "coder": ToolProfile(
                tools=[FileReadTool(), FileWriteTool(), BashTool()],
                system_prompt="You are an expert coder.",
            ),
        }

        agent = Agent(
            tools=[...],           # main agent tools
            tool_profiles=profiles, # sub-agent profiles
        )

    When the LLM spawns a background task with ``profile="researcher"``,
    the sub-agent automatically gets WebFetchTool + GrepTool.
    """
    tools: list[Tool] = field(default_factory=list)
    system_prompt: str = "You are a helpful assistant."
    max_turns: int = 15


@dataclass
class AgentConfig:
    """Engine-level configuration."""
    max_turns: int = 20
    compact_threshold: int = 100_000
    idle_check_interval: float = 1.0
    kairos_gate_config: GateConfig | None = None
    #: Opt in to the legacy slash-command layer for CLI-style hosts.
    enable_builtin_commands: bool = False
    #: Opt in to auto-register bundled skills into the command layer.
    enable_bundled_skills: bool = False
    #: When True, `QueryEngine` / `QueryParams` use `query_source="sdk"` (headless/SDK path).
    #: CLI print mode sets `agent._runtime_is_non_interactive` after init and overrides this.
    runtime_non_interactive: bool = False
    #: Load ``ccmini.tools`` / ``ccmini.hooks`` setuptools entry points from installed wheels.
    #: Disable on constrained hosts if you do not trust site-packages.
    enable_distribution_entry_points: bool = True


class Agent:
    """The main agent class.

    Usage (embedded resident mode)::

        agent = Agent(
            provider=ProviderConfig(type="anthropic", model="..."),
            system_prompt="You are a helpful assistant.",
            tools=[MyTool(), ...],
        )
        async with agent:
            async for event in agent.query("Hello"):
                print(event)

    Usage (explicit lifecycle)::

        agent = Agent(...)
        await agent.start()
        try:
            async for event in agent.query("Hello"):
                ...
        finally:
            await agent.stop()
    """

    def __init__(
        self,
        *,
        provider: ProviderConfig | BaseProvider,
        system_prompt: str | SystemPrompt,
        tools: list[Tool] | None = None,
        sub_agent_tools: list[Tool] | None = None,
        tool_profiles: dict[str, ToolProfile] | None = None,
        hooks: list[Hook] | None = None,
        config: AgentConfig | None = None,
        conversation_id: str | None = None,
        agent_id: str = "agent",
        attachment_collector: AttachmentCollector | None = None,
        fallback_config: FallbackConfig | None = None,
        token_budget: TokenBudget | None = None,
        summary_provider: Any | None = None,
        skill_prefetch: Any | None = None,
    ) -> None:
        if isinstance(provider, ProviderConfig):
            self._provider = create_provider(provider)
        else:
            self._provider = provider

        if isinstance(system_prompt, str):
            sp = SystemPrompt()
            sp.add_static(system_prompt)
            self._system_prompt = sp
        else:
            self._system_prompt = system_prompt

        self._tools = list(tools or [])
        self._sub_agent_tools: list[Tool] = list(sub_agent_tools or [])
        self._tool_profiles: dict[str, ToolProfile] = dict(tool_profiles or {})
        self._config = config or AgentConfig()
        self._conversation_id = conversation_id or uuid4().hex[:16]
        self._agent_id = agent_id
        self._fallback_config = fallback_config
        self._runtime_is_non_interactive = bool(self._config.runtime_non_interactive)

        self._hooks = list(hooks or [])
        self._hook_runner = HookRunner(self._hooks)
        self._idle_hooks: list[IdleHook] = [h for h in self._hooks if isinstance(h, IdleHook)]

        self._attachment_collector = attachment_collector or AttachmentCollector()
        ensure_companion_intro_source(self._attachment_collector)
        #: Mirrors ``feature('BUDDY')`` — defaults from ``ccmini.config``.
        self._buddy_enabled: bool = True
        #: When ``None``, :func:`buddy.prompt.get_companion_intro_attachment` uses global ``companionMuted``.
        self._companion_muted: bool | None = None
        self._working_directory: str = ""
        self._custom_system_prompt: str | None = None
        self._append_system_prompt: str | None = None
        self._user_context: dict[str, str] = {}
        self._system_context: dict[str, str] = {}
        self._session_store: SessionStore | None = None
        self._memory_store: JsonlMemoryStore | None = None
        self._memory_adapter: MemoryAdapter | None = None
        runtime_cfg: Any | None = None
        try:
            from .config import load_config

            runtime_cfg = load_config()
            self._buddy_enabled = bool(runtime_cfg.buddy_enabled)
            if self._config.kairos_gate_config is None:
                env_gate = GateConfig.from_env()
                self._config.kairos_gate_config = GateConfig(
                    kairos_enabled=bool(runtime_cfg.kairos_enabled),
                    brief_enabled=bool(runtime_cfg.kairos_brief_enabled),
                    proactive_enabled=bool(env_gate.proactive_enabled),
                    cron_enabled=bool(runtime_cfg.kairos_cron_enabled),
                    cron_durable=bool(runtime_cfg.kairos_cron_durable),
                    channels_enabled=bool(runtime_cfg.kairos_channels_enabled),
                    dream_enabled=bool(runtime_cfg.kairos_dream_enabled),
                )
        except Exception:
            logger.debug("Failed to load runtime defaults from config", exc_info=True)
        self._install_session_store(runtime_cfg)
        self._install_memory_runtime()
        self._install_runtime_services()
        self._usage_tracker = UsageTracker()
        self._stats_tracker = StatsTracker(self._conversation_id)
        self._token_budget = token_budget
        self._scheduler = TaskScheduler()
        self._summary_provider = summary_provider
        self._skill_prefetch = skill_prefetch

        self._command_registry = SlashCommandRegistry()
        if self._config.enable_builtin_commands:
            register_builtin_commands(self._command_registry)
            self._register_buddy_command()
        if self._config.enable_bundled_skills:
            register_bundled_skills_as_commands(self._command_registry)
        self._plugin_registry: PluginRegistry | None = None
        self._plugin_tools: list[Tool] = []
        self._install_plugin_runtime()

        self._task_manager = TaskManager()
        self._bg_runner = BackgroundAgentRunner(
            self._provider, self._task_manager, tool_resolver=self.resolve_tools,
        )

        self._messages: list[Message] = []
        self._running = False
        self._is_processing = False
        self._idle_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._idle_event_queue: asyncio.Queue[IdleAction] = asyncio.Queue()

        self._event_queue: asyncio.Queue[StreamEvent] = asyncio.Queue()
        self._runtime_notifications: list[StreamEvent] = []
        self._submit_task: asyncio.Task[None] | None = None
        self._last_reply: str = ""
        self._pending_client_run_id: str | None = None
        self._pending_client_calls: list[ToolCallEvent] = []

        self._event_signal = asyncio.Event()
        self._listeners: list[Callable[[StreamEvent], Any]] = []
        self._done_listeners: list[Callable[[str], Any]] = []
        self._current_turn_user_text: str = ""
        self._last_turn_tool_names: list[str] = []
        self._current_turn_tools: list[Tool] = list(self._tools)
        self._current_turn_state: Any | None = None

        self._event_bus = EventBus()
        self._summary_tracker: Any | None = None
        self._shutdown_in_progress = False
        self._installed_signals: list[int] = []
        self._coordinator_mode: Any | None = None
        self._mode_overridden_by_host = False
        self._kairos_prompt_sections_installed = False
        self._kairos_activated_by_agent = False
        self._kairos_proactive_stop: asyncio.Event | None = None
        self._kairos_proactive_task: asyncio.Task[Any] | None = None
        self._kairos_command_task: asyncio.Task[Any] | None = None
        self._kairos_cron_scheduler: Any | None = None
        self._kairos_suggestion_engine: Any | None = None
        self._kairos_last_autonomous_action_at: float = 0.0
        self._away_summary_manager: Any | None = None
        self._last_user_activity_at: float = 0.0
        self._permission_checker: PermissionChecker | None = None

    def _install_session_store(self, runtime_cfg: Any | None) -> None:
        """Attach transcript persistence as a core ccmini runtime service."""
        try:
            if runtime_cfg is not None and not bool(getattr(runtime_cfg, "session_persistence", True)):
                return
            session_dir = ""
            if runtime_cfg is not None:
                session_dir = str(getattr(runtime_cfg, "session_dir", "") or "").strip()
            self._session_store = SessionStore(session_dir or None)
        except Exception:
            logger.debug("Failed to initialize session store", exc_info=True)

    def _add_runtime_hook(self, hook: Hook, *, dedupe_type: bool = True) -> None:
        """Register a runtime-managed hook."""
        if dedupe_type and any(isinstance(existing, type(hook)) for existing in self._hooks):
            return
        self._hooks.append(hook)
        self._hook_runner.add(hook)
        if isinstance(hook, IdleHook):
            self._idle_hooks.append(hook)

    def _register_runtime_hook(self, hook: Hook) -> None:
        """Register a runtime-managed hook once."""
        self._add_runtime_hook(hook, dedupe_type=True)

    def _install_plugin_runtime(self) -> None:
        """Load project/global plugins and attach their commands, hooks, and tools."""
        try:
            plugin_dirs = discover_plugin_dirs_for_path(os.getcwd())
            registry = PluginRegistry(plugin_dirs=plugin_dirs)
            registry.load_all()
            self._plugin_registry = registry
            self._plugin_tools = list(registry.get_all_tools())
            if self._plugin_tools:
                self._tools.extend(self._plugin_tools)
            commands = registry.get_all_commands()
            if commands:
                self._command_registry.register_commands(commands)
            for hook in registry.get_all_hooks():
                self._add_runtime_hook(hook, dedupe_type=False)

            if self._config.enable_distribution_entry_points:
                from .distribution_plugins import load_hooks_from_entry_points, load_tools_from_entry_points

                dist_tools = load_tools_from_entry_points()
                if dist_tools:
                    self._tools.extend(dist_tools)
                    self._plugin_tools.extend(dist_tools)
                for hook in load_hooks_from_entry_points():
                    self._add_runtime_hook(hook, dedupe_type=False)
        except Exception:
            logger.debug("Failed to initialize plugin runtime", exc_info=True)

    def _install_memory_runtime(self) -> None:
        """Attach the ccmini memory stack and its default hooks."""
        try:
            store = JsonlMemoryStore(mini_agent_home())
            adapter = MemoryAdapter(
                store,
                consolidation_agent=ConsolidationAgent(
                    store=store,
                    provider=self._provider,
                ),
            )
            self._memory_store = store
            self._memory_adapter = adapter

            if not any(isinstance(source, MemoryAttachmentSource) for source in self._attachment_collector._sources):
                self._attachment_collector.add_source(MemoryAttachmentSource(adapter))

            project_root = os.getcwd()
            memory_dir = get_memory_dir(project_root)
            session_dir = str(
                getattr(self._session_store, "session_dir", mini_agent_home() / "sessions")
            )
            self._register_runtime_hook(
                SessionMemoryHook(
                    self._provider,
                    session_id=self._conversation_id,
                )
            )
            self._register_runtime_hook(
                ExtractMemoriesHook(
                    self._provider,
                    memory_dir=memory_dir,
                    project_root=project_root,
                )
            )
            self._register_runtime_hook(
                AutoDreamHook(
                    self._provider,
                    memory_dir=memory_dir,
                    session_dir=session_dir,
                )
            )
        except Exception:
            logger.debug("Failed to initialize runtime memory", exc_info=True)

    def _install_runtime_services(self) -> None:
        """Attach non-memory runtime services that should be on by default."""
        try:
            from .services.magic_docs import init_magic_doc_listener

            init_magic_doc_listener()
        except Exception:
            logger.debug("Failed to initialize Magic Docs listener", exc_info=True)

        try:
            self._register_runtime_hook(MagicDocsHook(self._provider))
        except Exception:
            logger.debug("Failed to initialize Magic Docs hook", exc_info=True)

        try:
            prompt_cfg = PromptSuggestionConfig()
            try:
                from .config import load_config

                runtime_cfg = load_config()
                prompt_cfg.enabled = bool(getattr(runtime_cfg, "prompt_suggestion_enabled", True))
                prompt_cfg.speculation_enabled = bool(getattr(runtime_cfg, "speculation_enabled", True))
            except Exception:
                logger.debug("Failed to load prompt suggestion runtime config", exc_info=True)

            self._register_runtime_hook(
                PromptSuggestionHook(self._provider, config=prompt_cfg)
            )
        except Exception:
            logger.debug("Failed to initialize prompt suggestion hook", exc_info=True)

        try:
            self._away_summary_manager = AwaySummaryManager(self._provider)
        except Exception:
            logger.debug("Failed to initialize away summary manager", exc_info=True)

    def _register_buddy_command(self) -> None:
        """Register the local ``/buddy`` command with ccmini runtime callbacks."""
        from .buddy import BuddyCommand, NurtureEngine, companion_user_id

        self._command_registry.register(
            BuddyCommand(
                user_id=companion_user_id(),
                nurture=NurtureEngine.load(),
                on_mute_toggle=lambda muted: setattr(self, "_companion_muted", muted),
            ),
        )
        self._compact_tracker = CompactTracker()
        self._compact_config = CompactConfig(
            context_window=self._config.compact_threshold + 33_000,
        )
        self._snip_config = SnipConfig(
            max_context_tokens=self._compact_config.context_window,
        )
        self._collapse_config = CollapseConfig()
        #: TS ``contentReplacementState`` / ``toolResultStorage.ts`` aggregate budget.
        self._content_replacement_state: Any = None
        #: Anthropic beta ``task_budget`` (``total`` / ``remaining``); updated by ``QueryEngine`` usage.
        self._task_budget: dict[str, int] | None = None

    def _ensure_content_replacement_state(self) -> None:
        """Provision ``ContentReplacementState`` when ``MINI_AGENT_AGGREGATE_TOOL_RESULT_BUDGET`` is set."""
        from pathlib import Path

        from .engine.result_budget import (
            create_content_replacement_state,
            is_aggregate_budget_feature_enabled,
            load_content_replacement_records,
            reconstruct_content_replacement_state,
        )

        if not is_aggregate_budget_feature_enabled():
            self._content_replacement_state = None
            return
        if self._content_replacement_state is not None:
            return
        store = getattr(self, "_session_store", None)
        session_dir = getattr(store, "session_dir", None) if store is not None else None
        records: list[Any] = []
        if session_dir is not None:
            records = load_content_replacement_records(Path(session_dir), self._conversation_id)
        if not self._messages:
            self._content_replacement_state = create_content_replacement_state()
        else:
            self._content_replacement_state = reconstruct_content_replacement_state(
                self._messages, records
            )

    def _append_content_replacement_records(self, records: list[Any]) -> None:
        """Append tool-result replacement records for resume (TS transcript callback)."""
        from pathlib import Path

        from .engine.result_budget import append_content_replacement_records as append_records

        if not records:
            return
        store = getattr(self, "_session_store", None)
        if store is None:
            return
        sd = getattr(store, "session_dir", None)
        if sd is None:
            return
        append_records(Path(sd), self._conversation_id, records)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start resident mode: background tasks, idle loop, session hooks."""
        if self._running:
            return
        self._running = True
        self._register_peer_session()
        self._restore_plan_mode_state()
        self._restore_coordinator_mode_state()
        self._install_signal_handlers()
        self._activate_kairos_runtime()
        await self._attempt_recovery()
        await self._hook_runner.run_session_start(agent=self)
        await self._start_kairos_runtime()
        if self._idle_hooks:
            self._idle_task = asyncio.create_task(self._idle_loop())

    async def stop(self) -> None:
        """Gracefully shut down: cancel background tasks, flush memory, session hooks."""
        self._running = False

        self.cancel_submit()
        if self._submit_task is not None:
            try:
                await self._submit_task
            except (asyncio.CancelledError, Exception):
                pass
            self._submit_task = None

        if self._idle_task is not None:
            self._idle_task.cancel()
            try:
                await self._idle_task
            except asyncio.CancelledError:
                pass
            self._idle_task = None

        await self._graceful_shutdown()
        await self._stop_kairos_runtime()
        self._remove_signal_handlers()
        self._unregister_peer_session()
        self._save_coordinator_mode_state()
        if self._kairos_activated_by_agent:
            deactivate_kairos()
            self._kairos_activated_by_agent = False

    async def __aenter__(self) -> Agent:
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def provider(self) -> BaseProvider:
        return self._provider

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools)

    @property
    def sub_agent_tools(self) -> list[Tool]:
        """Default tools for sub-agents when no profile is specified."""
        return list(self._sub_agent_tools)

    @property
    def tool_profiles(self) -> dict[str, ToolProfile]:
        return dict(self._tool_profiles)

    def resolve_tools(self, profile: str | None = None) -> tuple[list[Tool], str, int]:
        """Resolve tool set from a profile name.

        Returns:
            (tools, system_prompt, max_turns) for the sub-agent.

        Resolution order:
            1. Named profile from ``tool_profiles``
            2. ``sub_agent_tools`` (default sub-agent set)
            3. Empty list (sub-agent gets no tools)
        """
        if profile and profile in self._tool_profiles:
            p = self._tool_profiles[profile]
            return list(p.tools), p.system_prompt, p.max_turns
        if self._sub_agent_tools:
            return list(self._sub_agent_tools), "You are a helpful assistant.", 15
        return [], "You are a helpful assistant.", 15

    def add_tool_profile(self, name: str, profile: ToolProfile) -> None:
        """Register a named tool profile at runtime."""
        self._tool_profiles[name] = profile

    def remove_tool_profile(self, name: str) -> bool:
        """Remove a tool profile. Returns True if it existed."""
        return self._tool_profiles.pop(name, None) is not None

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def usage_tracker(self) -> UsageTracker:
        return self._usage_tracker

    @property
    def stats_tracker(self) -> StatsTracker:
        return self._stats_tracker

    def register_command(self, command: Any) -> None:
        """Register a slash command on this agent's command registry."""
        self._command_registry.register(command)

    @property
    def task_manager(self) -> TaskManager:
        return self._task_manager

    @property
    def background_runner(self) -> BackgroundAgentRunner:
        return self._bg_runner

    @property
    def scheduler(self) -> TaskScheduler:
        return self._scheduler

    @property
    def budget_status(self) -> BudgetStatus | None:
        """Current budget consumption, or None if no budget is set."""
        if self._token_budget is None:
            return None
        return BudgetStatus(self._token_budget, self._usage_tracker)

    @property
    def is_busy(self) -> bool:
        """True while a non-blocking ``submit()`` query is still running."""
        return self._submit_task is not None and not self._submit_task.done()

    @property
    def last_reply(self) -> str:
        """The final text reply from the most recent completed query."""
        return self._last_reply

    @property
    def pending_client_run_id(self) -> str | None:
        """Pending client-tool continuation token, if the turn is paused."""
        return self._pending_client_run_id

    @property
    def pending_client_calls(self) -> list[ToolCallEvent]:
        """Client-side tool calls awaiting host execution."""
        return list(self._pending_client_calls)

    @property
    def working_directory(self) -> str:
        """Agent-scoped working directory override, or the process cwd."""
        return self._working_directory or os.getcwd()

    @property
    def event_signal(self) -> asyncio.Event:
        """Set whenever new events arrive in the queue.

        External event loops can ``await agent.event_signal.wait()``
        instead of polling.  The signal is cleared after each
        ``drain_events()`` or ``poll_event()`` that empties the queue.
        """
        return self._event_signal

    def set_working_directory(self, path: str) -> None:
        """Set the working directory used for input resolution and tool context."""
        self._working_directory = str(Path(path).expanduser().resolve())
        self._refresh_memory_runtime_bindings()

    def set_custom_system_prompt(self, text: str) -> None:
        """Replace the default system prompt with host-provided text."""
        normalized = str(text).strip()
        self._custom_system_prompt = normalized or None

    def set_append_system_prompt(self, text: str) -> None:
        """Append host-provided text after the composed system prompt."""
        normalized = str(text).strip()
        self._append_system_prompt = normalized or None

    def set_user_context(self, values: dict[str, Any]) -> None:
        """Inject additional host-supplied user-context reminder values."""
        self._user_context = {
            str(key): str(value)
            for key, value in dict(values or {}).items()
            if str(key).strip() and str(value).strip()
        }

    def set_system_context(self, values: dict[str, Any]) -> None:
        """Inject additional host-supplied system-context reminder values."""
        self._system_context = {
            str(key): str(value)
            for key, value in dict(values or {}).items()
            if str(key).strip() and str(value).strip()
        }

    def on_event(self, callback: Callable[[StreamEvent], Any]) -> Callable[[], None]:
        """Register a listener called for every StreamEvent.

        Works for both ``query()`` (blocking) and ``submit()``
        (non-blocking) events.  Returns an unsubscribe function.

        The callback can be sync or async:
        - sync: ``def handler(event): ...``
        - async: ``async def handler(event): ...``
        """
        self._listeners.append(callback)
        def unsubscribe() -> None:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass
        return unsubscribe

    def on_done(self, callback: Callable[[str], Any]) -> Callable[[], None]:
        """Register a listener called when a submit() query completes.

        The callback receives the final reply text.  Returns an
        unsubscribe function.
        """
        self._done_listeners.append(callback)
        def unsubscribe() -> None:
            try:
                self._done_listeners.remove(callback)
            except ValueError:
                pass
        return unsubscribe

    def _annotate_event(
        self,
        event: StreamEvent,
        *,
        conversation_id: str = "",
    ) -> StreamEvent:
        """Attach stable conversation/turn correlation fields to an event."""
        conv_id = str(
            conversation_id
            or getattr(event, "conversation_id", "")
            or self._conversation_id
        ).strip()
        current_turn = getattr(self, "_current_turn_state", None)
        turn_id = str(
            getattr(event, "turn_id", "")
            or getattr(current_turn, "turn_id", "")
            or ""
        ).strip()

        if hasattr(event, "conversation_id") and not getattr(event, "conversation_id", ""):
            event.conversation_id = conv_id  # type: ignore[attr-defined]
        if hasattr(event, "turn_id") and turn_id and not getattr(event, "turn_id", ""):
            event.turn_id = turn_id  # type: ignore[attr-defined]

        tool_use_ids = getattr(event, "tool_use_ids", None)
        if (
            hasattr(event, "tool_use_id")
            and not getattr(event, "tool_use_id", "")
            and isinstance(tool_use_ids, list)
            and len(tool_use_ids) == 1
        ):
            event.tool_use_id = str(tool_use_ids[0])  # type: ignore[attr-defined]

        calls = getattr(event, "calls", None)
        if isinstance(calls, list):
            for call in calls:
                if isinstance(call, ToolCallEvent):
                    self._annotate_event(call, conversation_id=conv_id)
                    if hasattr(call, "turn_id") and turn_id and not getattr(call, "turn_id", ""):
                        call.turn_id = turn_id
                    event_run_id = str(getattr(event, "run_id", "") or "").strip()
                    if hasattr(call, "run_id") and event_run_id and not getattr(call, "run_id", ""):
                        call.run_id = event_run_id

        return event

    def _fire_event(self, event: StreamEvent) -> None:
        """Dispatch an event to all registered listeners + set signal."""
        event = self._annotate_event(event)
        self._event_signal.set()
        for cb in self._listeners:
            try:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception:
                logger.debug("Event listener error", exc_info=True)

    def _fire_done(self, reply: str) -> None:
        """Notify all done-listeners that the query finished."""
        for cb in self._done_listeners:
            try:
                result = cb(reply)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception:
                logger.debug("Done listener error", exc_info=True)

    # ------------------------------------------------------------------
    # Non-blocking query mode (submit / poll / drain)
    # ------------------------------------------------------------------

    def submit(
        self,
        user_text: str,
        *,
        conversation_id: str | None = None,
        user_id: str = "",
        metadata: dict[str, Any] | None = None,
        attachments: list[Any] | None = None,
        turn_id: str | None = None,
        on_event: Any | None = None,
    ) -> None:
        """Non-blocking: submit a user message and return immediately.

        The query runs as an ``asyncio.Task`` in the background.
        Events stream into an internal queue that the caller can
        read via ``poll_event()`` / ``drain_events()``.

        Args:
            user_text: The user message.
            conversation_id: Optional override.
            on_event: Optional sync callback ``(event) -> None`` called
                      for each event as it arrives (from the background
                      task, not the caller's thread).

        Raises:
            RuntimeError: If a previous submit is still running.
        """
        if self.is_busy:
            raise RuntimeError(
                "A submit() query is already in progress. "
                "Wait for it to finish or call cancel_submit() first."
            )

        self._submit_task = asyncio.ensure_future(
            self._submit_pump(
                user_text,
                conversation_id,
                on_event,
                user_id=user_id,
                metadata=metadata,
                attachments=attachments,
                turn_id=turn_id,
            )
        )

    def submit_user_input(
        self,
        text: str,
        *,
        conversation_id: str | None = None,
        user_id: str = "",
        metadata: dict[str, Any] | None = None,
        attachments: list[Any] | None = None,
    ) -> str:
        """Stable host entrypoint for non-blocking user turns.

        Returns the assigned ``turn_id`` for correlation with emitted events.
        """
        turn_id = uuid4().hex[:16]
        self.submit(
            text,
            conversation_id=conversation_id,
            user_id=user_id,
            metadata=metadata,
            attachments=attachments,
            turn_id=turn_id,
        )
        return turn_id

    async def _submit_pump(
        self,
        user_text: str,
        conversation_id: str | None,
        on_event: Any | None,
        *,
        user_id: str = "",
        metadata: dict[str, Any] | None = None,
        attachments: list[Any] | None = None,
        turn_id: str | None = None,
    ) -> None:
        """Consume the query generator and push events to the queue."""
        try:
            async for event in self.query(
                user_text,
                conversation_id=conversation_id,
                user_id=user_id,
                metadata=metadata,
                attachments=attachments,
                turn_id=turn_id,
            ):
                event = self._annotate_event(event, conversation_id=conversation_id or self._conversation_id)
                await self._event_queue.put(event)
                self._fire_event(event)
                if on_event is not None:
                    try:
                        on_event(event)
                    except Exception:
                        pass
                if isinstance(event, CompletionEvent):
                    self._last_reply = event.text
        except Exception as exc:
            err = self._annotate_event(
                ErrorEvent(error=str(exc), recoverable=False),
                conversation_id=conversation_id or self._conversation_id,
            )
            await self._event_queue.put(err)
            self._fire_event(err)

        self._fire_done(self._last_reply)

    def poll_event(self) -> StreamEvent | None:
        """Non-blocking: pop one event from the queue, or None."""
        try:
            event = self._event_queue.get_nowait()
            if self._event_queue.empty():
                self._event_signal.clear()
            return event
        except asyncio.QueueEmpty:
            self._event_signal.clear()
            return None

    async def wait_event(self, timeout: float = 0.0) -> StreamEvent | None:
        """Async: wait up to ``timeout`` seconds for the next event."""
        try:
            if timeout > 0:
                return await asyncio.wait_for(
                    self._event_queue.get(), timeout=timeout
                )
            return self._event_queue.get_nowait()
        except (asyncio.QueueEmpty, asyncio.TimeoutError):
            return None

    def drain_events(self) -> list[StreamEvent]:
        """Non-blocking: drain all queued events at once."""
        events: list[StreamEvent] = []
        while True:
            try:
                events.append(self._event_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        self._event_signal.clear()
        return events

    async def wait_reply(self, timeout: float | None = None) -> str:
        """Block until the current submit() finishes, return the final reply.

        Convenience for callers that want submit-then-wait semantics
        (submit in one place, await result in another).
        """
        if self._submit_task is None:
            return self._last_reply
        try:
            if timeout is not None:
                await asyncio.wait_for(
                    asyncio.shield(self._submit_task), timeout=timeout
                )
            else:
                await self._submit_task
        except asyncio.TimeoutError:
            pass
        return self._last_reply

    def cancel_submit(self) -> bool:
        """Cancel the in-flight non-blocking query, if any."""
        if self._submit_task is not None and not self._submit_task.done():
            self._submit_task.cancel()
            return True
        return False

    def _normalize_host_content(
        self,
        *,
        text: str = "",
        content: Any = None,
        attachments: list[Any] | None = None,
    ) -> str | list[dict[str, Any]]:
        """Normalize host-provided rich content into message/tool-result blocks."""
        from .engine.input_processor import process_attachments

        blocks: list[dict[str, Any]] = []
        deferred: list[dict[str, Any]] = []

        if content is not None:
            if isinstance(content, list):
                attachments = list(content) + list(attachments or [])
            elif isinstance(content, dict):
                attachments = [content, *(attachments or [])]
            elif str(content).strip():
                text = str(content)

        if text:
            blocks.append({"type": "text", "text": text})

        for attachment in attachments or []:
            if isinstance(attachment, TextBlock):
                blocks.append({"type": "text", "text": attachment.text})
                continue
            if isinstance(attachment, ImageBlock):
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": attachment.media_type,
                        "data": attachment.source,
                    },
                })
                continue
            if isinstance(attachment, DocumentBlock):
                blocks.append({
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": attachment.media_type,
                        "data": attachment.source,
                    },
                })
                continue

            if not isinstance(attachment, dict):
                blocks.append({"type": "text", "text": str(attachment)})
                continue

            att_type = str(attachment.get("type", "") or "").strip()
            if att_type == "text":
                blocks.append({"type": "text", "text": str(attachment.get("text", ""))})
                continue
            if att_type in {"image", "document"}:
                source = attachment.get("source")
                if isinstance(source, dict) and source.get("type") == "base64":
                    blocks.append({
                        "type": att_type,
                        "source": dict(source),
                    })
                elif attachment.get("path"):
                    deferred.append({
                        "type": "image" if att_type == "image" else "file",
                        "path": str(attachment.get("path", "")),
                    })
                continue
            if att_type in {"file", "url", "audio"}:
                deferred.append(dict(attachment))
                continue
            blocks.append({"type": "text", "text": str(attachment)})

        if deferred:
            for processed in process_attachments(deferred, cwd=self.working_directory):
                item = processed.content
                if isinstance(item, str):
                    blocks.append({"type": "text", "text": item})
                elif isinstance(item, dict):
                    blocks.append(dict(item))
                else:
                    blocks.append({"type": "text", "text": str(item)})

        if not blocks:
            return ""
        if len(blocks) == 1 and blocks[0].get("type") == "text":
            return str(blocks[0].get("text", ""))
        return normalize_tool_result_content(blocks)

    def _normalize_tool_result_blocks(
        self,
        results: list[ToolResultBlock | HostToolResult | dict[str, Any]],
    ) -> list[ToolResultBlock]:
        normalized: list[ToolResultBlock] = []
        for item in results:
            if isinstance(item, ToolResultBlock):
                normalized.append(
                    ToolResultBlock(
                        tool_use_id=item.tool_use_id,
                        content=normalize_tool_result_content(item.content),
                        is_error=item.is_error,
                        metadata=dict(item.metadata),
                    )
                )
                continue

            if isinstance(item, HostToolResult):
                normalized.append(
                    ToolResultBlock(
                        tool_use_id=item.tool_use_id,
                        content=self._normalize_host_content(
                            text=item.text,
                            attachments=item.attachments,
                        ),
                        is_error=item.is_error,
                        metadata=dict(item.metadata),
                    )
                )
                continue

            payload = dict(item)
            tool_use_id = str(payload.get("tool_use_id", "") or "").strip()
            if not tool_use_id:
                raise ValueError("Host tool result is missing tool_use_id")
            content_value = payload["content"] if "content" in payload else payload.get("text", "")
            normalized.append(
                ToolResultBlock(
                    tool_use_id=tool_use_id,
                    content=self._normalize_host_content(
                        content=content_value,
                        attachments=list(payload.get("attachments", []) or []),
                    ),
                    is_error=bool(payload.get("is_error", False)),
                    metadata=dict(payload.get("metadata", {})),
                )
            )
        return normalized

    def _dispatch_runtime_event(self, event: StreamEvent) -> None:
        """Route a runtime event to the live queue or deferred notification buffer."""
        event = self._annotate_event(event)
        submit_inflight = (
            self._submit_task is not None
            and not self._submit_task.done()
        )
        if self._is_processing and submit_inflight:
            self._event_queue.put_nowait(event)
            self._fire_event(event)
            return
        if self._is_processing:
            self._runtime_notifications.append(event)  # type: ignore[arg-type]
            return
        self._event_queue.put_nowait(event)
        self._fire_event(event)

    def publish_host_event(self, event: HostEvent) -> None:
        """Inject a host/runtime event into conversation state and host-visible event flow."""
        conversation_id = str(event.conversation_id or self._conversation_id).strip() or self._conversation_id
        metadata = dict(event.metadata)
        if event.event_type:
            metadata.setdefault("event_type", event.event_type)
        if event.turn_id:
            metadata.setdefault("turn_id", event.turn_id)
        metadata.setdefault("host_role", event.role)
        metadata.setdefault("host_event", True)

        content = self._normalize_host_content(
            text=(
                event.text
                if event.role != "system"
                else (
                    f"<host-event type=\"{event.event_type or 'runtime'}\" role=\"{event.role}\">\n"
                    f"{event.text}\n"
                    "</host-event>"
                ).strip()
            ),
            attachments=event.attachments,
        )
        message_content: str | list[Any]
        if isinstance(content, list):
            message_blocks: list[Any] = []
            for block in content:
                block_type = str(block.get("type", "") or "").strip()
                if block_type == "text":
                    message_blocks.append(TextBlock(text=str(block.get("text", ""))))
                elif block_type == "image":
                    source = block.get("source", {})
                    if isinstance(source, dict) and source.get("type") == "base64":
                        message_blocks.append(
                            ImageBlock(
                                source=str(source.get("data", "")),
                                media_type=str(source.get("media_type", "image/png")),
                            )
                        )
                    else:
                        message_blocks.append(TextBlock(text="[image]"))
                elif block_type == "document":
                    source = block.get("source", {})
                    if isinstance(source, dict) and source.get("type") == "base64":
                        message_blocks.append(
                            DocumentBlock(
                                source=str(source.get("data", "")),
                                media_type=str(source.get("media_type", "application/pdf")),
                            )
                        )
                    else:
                        message_blocks.append(TextBlock(text="[document]"))
                else:
                    message_blocks.append(TextBlock(text=str(block)))
            message_content = message_blocks
        else:
            message_content = content
        message = Message(
            role="assistant" if event.role == "assistant" else "user",
            content=message_content,
            metadata={
                "uuid": uuid4().hex,
                **({"assistantId": uuid4().hex} if event.role == "assistant" else {}),
                "timestamp": time.time(),
                "isMeta": True,
                "conversation_id": conversation_id,
                **metadata,
            },
        )
        self._messages.append(message)
        self._persist_session_snapshot()

        if self._memory_store is not None:
            with contextlib.suppress(Exception):
                self._memory_store.append_event_record(
                    conversation_id,
                    {
                        "conversation_id": conversation_id,
                        "turn_id": event.turn_id,
                        "event_type": event.event_type,
                        "role": event.role,
                        "text": event.text,
                        "metadata": dict(event.metadata),
                    },
                )

        stream_event = TextEvent(
            text=event.text or f"[host_event:{event.event_type or 'runtime'}]",
            conversation_id=conversation_id,
            turn_id=event.turn_id,
            metadata={
                "event_type": event.event_type,
                "role": event.role,
                **dict(event.metadata),
            },
        )
        self._dispatch_runtime_event(stream_event)

    def publish_runtime_event(self, event: HostEvent) -> None:
        """Backward-compatible alias for :meth:`publish_host_event`."""
        self.publish_host_event(event)

    # ------------------------------------------------------------------
    # Background agent tasks
    # ------------------------------------------------------------------

    def spawn_background(
        self,
        *,
        name: str,
        prompt: str,
        profile: str | None = None,
        system_prompt: str | None = None,
        tools: list[Tool] | None = None,
        include_context: bool = False,
        max_turns: int | None = None,
    ) -> str:
        """Spawn an agent task in background. Returns task ID.

        The foreground conversation continues immediately.  Background
        completions are delivered via ``drain_background_completions()``
        or automatically injected as notifications between query turns.

        Args:
            profile: Named tool profile to use. When set, ``tools``,
                ``system_prompt`` and ``max_turns`` are loaded from
                the profile (explicit args still override).
        """
        return self._bg_runner.spawn(
            name=name,
            prompt=prompt,
            profile=profile,
            system_prompt=system_prompt,
            tools=tools,
            context_messages=list(self._messages) if include_context else None,
            max_turns=max_turns,
        )

    def drain_background_completions(self) -> list[BackgroundResult]:
        """Return all completed background task results (non-blocking)."""
        return self._bg_runner.drain_completions()

    def list_background_tasks(self, *, include_completed: bool = False) -> list[Any]:
        """List background tasks known to this agent."""
        return self._task_manager.list_tasks(include_completed=include_completed)

    def get_task(self, task_id: str) -> Any | None:
        """Fetch a background task by ID or stable human-readable name."""
        resolved = self._bg_runner.resolve_task_ref(task_id) or task_id
        return self._bg_runner.get_status(resolved)

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a background task by ID or stable human-readable name."""
        resolved = self._bg_runner.resolve_task_ref(task_id) or task_id
        return self._bg_runner.cancel(resolved)

    def send_message_to_task(self, task_id: str, message: str) -> bool:
        """Send a follow-up message to a background task."""
        return self._bg_runner.send_message(task_id, message)

    def _ensure_coordinator_mode(self) -> Any:
        from .delegation.coordinator import CoordinatorMode

        if self._coordinator_mode is None:
            self._coordinator_mode = CoordinatorMode()
        return self._coordinator_mode

    def get_mode(self) -> str:
        """Return the current instance mode: ``normal`` or ``coordinator``."""
        coordinator = self._coordinator_mode
        return "coordinator" if bool(getattr(coordinator, "is_active", False)) else "normal"

    def set_mode(self, mode: str) -> None:
        """Set the instance mode without relying on global env toggles."""
        normalized = str(mode).strip().lower()
        if normalized not in {"normal", "coordinator"}:
            raise ValueError("mode must be 'normal' or 'coordinator'")

        self._mode_overridden_by_host = True
        coordinator = self._ensure_coordinator_mode()
        if normalized == "normal":
            coordinator.deactivate(sync_env=False)
            return

        worker_tools: list[str] = []
        try:
            from .delegation.multi_agent import AgentTool

            for tool in self._tools:
                if isinstance(tool, AgentTool):
                    worker_tools = [
                        child.name
                        for child in getattr(tool, "_parent_tools", [])
                    ]
                    break
        except Exception:
            logger.debug("Failed to inspect worker tools for coordinator mode", exc_info=True)

        coordinator.activate(
            coordinator_tool_names=[tool.name for tool in self._tools] or None,
            worker_tool_names=worker_tools or None,
            sync_env=False,
        )

    def set_runtime_stores(
        self,
        *,
        session_store: SessionStore | None = None,
        memory_store: Any | None = None,
        memory_adapter: MemoryAdapter | None = None,
    ) -> None:
        """Install host-provided session and memory stores/adapters."""
        if session_store is not None:
            self._session_store = session_store

        if memory_adapter is None and memory_store is not None:
            memory_adapter = MemoryAdapter(
                memory_store,
                consolidation_agent=ConsolidationAgent(
                    store=memory_store,
                    provider=self._provider,
                ),
            )
        if memory_store is None and memory_adapter is not None:
            memory_store = getattr(memory_adapter, "store", None)
            if memory_store is None:
                raise ValueError("memory_adapter must expose a store attribute")

        if memory_store is not None:
            self._memory_store = memory_store
        if memory_adapter is not None:
            self._memory_adapter = memory_adapter

        if self._memory_adapter is not None:
            sources = getattr(self._attachment_collector, "_sources", None)
            if isinstance(sources, list):
                sources[:] = [
                    source
                    for source in sources
                    if not isinstance(source, MemoryAttachmentSource)
                ]
            if not any(
                isinstance(source, MemoryAttachmentSource)
                for source in getattr(self._attachment_collector, "_sources", [])
            ):
                self._attachment_collector.add_source(MemoryAttachmentSource(self._memory_adapter))

        self._refresh_memory_runtime_bindings()

    def set_memory_backend(
        self,
        memory_store: Any,
        *,
        memory_adapter: MemoryAdapter | None = None,
    ) -> None:
        """Install a host-provided memory backend."""
        self.set_runtime_stores(memory_store=memory_store, memory_adapter=memory_adapter)

    def set_memory_roots(
        self,
        *,
        profile_root: str,
        session_root: str | None = None,
        memory_root: str | None = None,
    ) -> None:
        """Point memory/session persistence into host-managed directories."""
        profile_path = Path(profile_root).expanduser().resolve()
        transcript_root = (
            Path(session_root).expanduser().resolve()
            if session_root
            else profile_path / "sessions"
        )
        raw_session_root = (
            Path(session_root).expanduser().resolve() / "streams"
            if session_root
            else profile_path / "session"
        )
        memory_path = (
            Path(memory_root).expanduser().resolve()
            if memory_root
            else profile_path / "memory"
        )

        self.set_runtime_stores(
            session_store=SessionStore(transcript_root),
            memory_store=JsonlMemoryStore(
                profile_path,
                memory_root=memory_path,
                session_root=raw_session_root,
            ),
        )

    def _refresh_memory_runtime_bindings(self) -> None:
        """Repoint default runtime hooks at the currently installed stores."""
        memory_dir = str(
            getattr(getattr(self, "_memory_store", None), "memory_root", "")
            or get_memory_dir(self.working_directory)
        )
        project_root = self.working_directory
        session_dir = str(
            getattr(getattr(self, "_session_store", None), "session_dir", "")
            or ""
        )

        have_session_hook = False
        have_extract_hook = False
        have_auto_dream_hook = False
        for hook in self._hooks:
            if isinstance(hook, SessionMemoryHook):
                hook._session_id = self._conversation_id
                have_session_hook = True
            elif isinstance(hook, ExtractMemoriesHook):
                hook._memory_dir = memory_dir
                hook._project_root = project_root
                have_extract_hook = True
            elif isinstance(hook, AutoDreamHook):
                hook._memory_dir = memory_dir
                hook._session_dir = session_dir
                have_auto_dream_hook = True

        if not have_session_hook:
            self._register_runtime_hook(
                SessionMemoryHook(
                    self._provider,
                    session_id=self._conversation_id,
                )
            )
        if not have_extract_hook:
            self._register_runtime_hook(
                ExtractMemoriesHook(
                    self._provider,
                    memory_dir=memory_dir,
                    project_root=project_root,
                )
            )
        if not have_auto_dream_hook:
            self._register_runtime_hook(
                AutoDreamHook(
                    self._provider,
                    memory_dir=memory_dir,
                    session_dir=session_dir,
                )
            )

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------

    async def query(
        self,
        user_text: str,
        *,
        conversation_id: str | None = None,
        user_id: str = "",
        metadata: dict[str, Any] | None = None,
        attachments: list[Any] | None = None,
        turn_id: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Process a user message, yielding stream events.

        This is the primary interface for the host application.
        Slash commands (``/name args``) are intercepted and handled
        locally without calling the LLM.

        Between turns, any completed background tasks are yielded as
        TextEvent notifications so the user stays informed.
        """
        conv_id = conversation_id or self._conversation_id
        for note in self._ingest_session_mailbox_messages():
            yield self._annotate_event(note, conversation_id=conv_id)
        for note in self._ingest_team_mailbox_messages():
            yield self._annotate_event(note, conversation_id=conv_id)
        # Deliver background completions before the turn starts
        for note in self._yield_background_notifications():
            yield self._annotate_event(note, conversation_id=conv_id)
        for note in self._yield_runtime_notifications():
            yield self._annotate_event(note, conversation_id=conv_id)
        for note in await self._consume_away_summary_notifications():
            yield self._annotate_event(note, conversation_id=conv_id)

        self._is_processing = True
        self._update_peer_status("busy")
        start_prevent_sleep()

        try:
            engine = QueryEngine(self)
            async for event in engine.submit_message(
                user_text,
                conversation_id=conv_id,
                metadata=metadata,
                attachments=attachments,
                user_id=user_id,
                turn_id=turn_id,
            ):
                if isinstance(event, CompletionEvent):
                    self._last_reply = event.text
                yield self._annotate_event(event, conversation_id=conv_id)

        finally:
            stop_prevent_sleep()
            self._update_peer_status("idle")
            self._is_processing = False

        current_turn = getattr(self, "_current_turn_state", None)
        if str(getattr(getattr(current_turn, "phase", None), "value", getattr(current_turn, "phase", ""))) in {"completed", "failed"}:
            self._current_turn_state = None

        # Deliver any completions that arrived during this turn
        for note in self._yield_background_notifications():
            yield self._annotate_event(note, conversation_id=conv_id)
        for note in self._yield_runtime_notifications():
            yield self._annotate_event(note, conversation_id=conv_id)

    def _get_session_memory_content(self) -> str | None:
        try:
            from .services.session_memory import get_session_memory_content

            return get_session_memory_content(self._conversation_id)
        except Exception:
            return None

    def _record_runtime_memory(
        self,
        *,
        user_text: str,
        reply_text: str,
        had_tools: bool,
    ) -> None:
        """Persist turn traces into the 3-layer runtime memory store."""
        adapter = getattr(self, "_memory_adapter", None)
        if adapter is None:
            return
        if not user_text and not reply_text:
            return
        try:
            turn_id = f"turn-{int(time.time() * 1000)}"
            if user_text:
                adapter.record_turn(
                    self._conversation_id,
                    "user",
                    user_text,
                    turn_id=turn_id,
                )
            if reply_text:
                adapter.record_turn(
                    self._conversation_id,
                    "assistant",
                    reply_text,
                    turn_id=turn_id,
                )
            for tool_name in self._last_turn_tool_names:
                adapter.record_tool(
                    self._conversation_id,
                    tool_name,
                    reply_text[:500],
                    turn_id=turn_id,
                )
            adapter.record_cognitive_event(
                conversation_id=self._conversation_id,
                agent_id=self._agent_id,
                turn_id=turn_id,
                user_text=user_text,
                reply=reply_text,
                had_tools=had_tools,
            )
            if getattr(adapter, "consolidation_agent", None) is not None:
                self.schedule_background(
                    adapter.consolidate_turn(
                        conversation_id=self._conversation_id,
                        agent_id=self._agent_id,
                        turn_id=turn_id,
                        user_text=user_text,
                        reply=reply_text,
                    )
                )
        except Exception:
            logger.debug("Failed to record runtime memory", exc_info=True)

    def _persist_session_snapshot(self) -> None:
        """Persist transcript + metadata when a session store is attached."""
        store = getattr(self, "_session_store", None)
        if store is None:
            return

        try:
            from .session.store import SessionMetadata

            store.save_messages(self.conversation_id, self.messages)
            title = getattr(self, "_session_name", "").strip()
            summary = self.usage_tracker.summary()
            meta = store.load_metadata(self.conversation_id) or SessionMetadata(
                session_id=self.conversation_id,
                created_at=time.time(),
            )
            if title:
                meta.title = title
            meta.session_id = self.conversation_id
            meta.cwd = self.working_directory
            meta.updated_at = time.time()
            meta.message_count = len(self.messages)
            meta.total_tokens = summary.get("total_input_tokens", 0) + summary.get("total_output_tokens", 0)
            meta.total_cost_usd = self.usage_tracker.total_cost()
            meta.model = self.provider.model_name
            current_turn = getattr(self, "_current_turn_state", None)
            if current_turn is not None:
                meta.last_stop_reason = getattr(current_turn, "stop_reason", "") or ""
                phase = getattr(current_turn, "phase", "")
                meta.turn_phase = getattr(phase, "value", phase) or ""
                meta.pending_run_id = getattr(current_turn, "pending_run_id", "") or ""
                meta.pending_tool_count = len(getattr(self, "_pending_client_calls", []) or [])
            stats_tracker = getattr(self, "_stats_tracker", None)
            if stats_tracker is not None:
                with contextlib.suppress(Exception):
                    stats_tracker.save()
            store.save_metadata(meta)
        except Exception:
            logger.debug("Failed to persist session snapshot", exc_info=True)

    def _activate_kairos_runtime(self) -> None:
        """Bootstrap Kairos from environment/runtime gate config."""
        gate = self._config.kairos_gate_config or GateConfig.from_env()
        set_gate_config(gate)
        trusted = check_directory_trust(os.getcwd())
        activated = activate_kairos(
            trust_accepted=trusted,
            gate_config=gate,
        )
        self._kairos_activated_by_agent = activated
        if activated:
            self._install_kairos_prompt_sections()

    def _install_kairos_prompt_sections(self) -> None:
        """Install Kairos prompt providers once so assistant mode can steer turns."""
        if self._kairos_prompt_sections_installed:
            return
        self._system_prompt.add_dynamic(
            "kairos_assistant",
            get_assistant_system_prompt_addendum,
        )
        self._system_prompt.add_dynamic(
            "kairos_proactive",
            get_proactive_system_prompt,
        )
        self._system_prompt.add_dynamic(
            "kairos_brief",
            get_brief_system_prompt,
        )
        self._system_prompt.add_dynamic(
            "kairos_channels",
            get_channels_system_prompt,
        )
        self._kairos_prompt_sections_installed = True

    async def _start_kairos_runtime(self) -> None:
        """Start the practical Kairos runtime loops for active sessions."""
        if not self._kairos_activated_by_agent:
            return

        try:
            from .kairos import (
                CronScheduler,
                CronSchedulerConfig,
                IdleLevel,
                ProactiveSuggestionEngine,
                TaskStore,
                create_cron_task,
                create_dream_cron_task,
                get_channel_persistence,
                get_channel_registry,
                get_gate_config,
                get_idle_detector,
                get_wake_on_event,
                run_proactive_loop,
            )
            from .kairos.sleep import (
                QueuedCommand,
                WakeReason,
                WakeTriggerType,
                get_command_queue,
                sleep_until_wake,
            )
            from .services import send_notification

            gate = get_gate_config()
            idle_detector = get_idle_detector()
            suggestion_engine = ProactiveSuggestionEngine()
            self._kairos_suggestion_engine = suggestion_engine
            wake_dispatcher = get_wake_on_event()
            command_queue = get_command_queue()
            channel_persistence = get_channel_persistence()

            async def _process_pending_commands() -> bool:
                pending_commands = await command_queue.drain(max_items=8)
                if not pending_commands:
                    return False
                for cmd in pending_commands:
                    self._messages.append(
                        user_message(
                            cmd.content,
                            _kairos_trigger=True,
                            source=cmd.source,
                            trigger_metadata=dict(cmd.metadata),
                        )
                    )
                    try:
                        trigger_type = {
                            "channel": WakeTriggerType.CHANNEL,
                            "cron": WakeTriggerType.SCHEDULE,
                            "user": WakeTriggerType.MESSAGE,
                        }.get(cmd.source, WakeTriggerType.CUSTOM)
                        await wake_dispatcher.fire(trigger_type, cmd)
                    except Exception:
                        logger.debug("Wake trigger dispatch failed", exc_info=True)

                    if cmd.source in {"channel", "cron"}:
                        self._bg_runner.spawn(
                            name=f"kairos-{cmd.source}",
                            prompt=cmd.content,
                            context_messages=self._messages[-12:],
                            max_turns=8,
                        )
                        if cmd.source == "channel":
                            channel_tag = cmd.metadata.get("channel", "")
                            if channel_tag:
                                try:
                                    channel_persistence.mark_read(channel_tag)
                                except Exception:
                                    logger.debug("Failed to clear unread channel message", exc_info=True)
                return True

            async def _wake_on_channel(cmd: Any) -> None:
                channel_tag = cmd.metadata.get("channel", "")
                if channel_tag:
                    await send_notification(
                        f"Channel message: {channel_tag}",
                        (cmd.content or "")[:200],
                    )

            async def _wake_on_schedule(cmd: Any) -> None:
                await send_notification(
                    "Scheduled task ready",
                    (cmd.content or "")[:160],
                )

            wake_dispatcher.wake_on_channel(_wake_on_channel, label="notify-channel")
            wake_dispatcher.wake_on_schedule(_wake_on_schedule, label="notify-cron")

            for channel_tag in channel_persistence.list_channels_with_unread():
                for msg in channel_persistence.load_unread(channel_tag):
                    command_queue.enqueue_nowait(
                        QueuedCommand(
                            source="channel",
                            content=msg.content,
                            priority="next",
                            metadata={
                                "channel": msg.channel_tag,
                                "sender": msg.sender,
                            },
                        )
                    )

            async def _command_watcher(stop_event: asyncio.Event) -> None:
                while not stop_event.is_set():
                    try:
                        await asyncio.wait_for(command_queue.wake_event.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    command_queue.clear_wake()
                    if stop_event.is_set():
                        break
                    try:
                        await _process_pending_commands()
                    except Exception:
                        logger.debug("Kairos command watcher failed", exc_info=True)

            async def _on_tick(_tick_msg: dict[str, Any]) -> bool:
                level = await idle_detector.tick()
                await suggestion_engine.evaluate(level)
                if await _process_pending_commands():
                    return True

                if level == IdleLevel.ACTIVE or self._is_processing:
                    return False

                if time.time() - self._kairos_last_autonomous_action_at < 300:
                    wake_result = await sleep_until_wake(
                        min(30.0, max(5.0, self._kairos_last_autonomous_action_at - time.time() + 300)),
                        queue=command_queue,
                    )
                    return wake_result.reason != WakeReason.TIMEOUT

                self._kairos_last_autonomous_action_at = time.time()
                self._bg_runner.spawn(
                    name="kairos-idle-check",
                    prompt=(
                        "Autonomous idle check. Review the recent session state, "
                        "pending background tasks, and obvious next steps. "
                        "If there is meaningful maintenance or follow-up work, do it. "
                        "Otherwise return a short no-op summary."
                    ),
                    context_messages=self._messages[-12:],
                    max_turns=6,
                )
                return True

            self._kairos_proactive_stop = asyncio.Event()
            self._kairos_command_task = asyncio.create_task(
                _command_watcher(self._kairos_proactive_stop)
            )
            self._kairos_proactive_task = asyncio.create_task(
                run_proactive_loop(
                    _on_tick,
                    stop_event=self._kairos_proactive_stop,
                )
            )

            if gate.cron_enabled:
                store = TaskStore()
                tasks = store.load()
                if gate.dream_enabled and not any(t.name == "nightly_dream" for t in tasks):
                    create_cron_task(store, **create_dream_cron_task())

                scheduler = CronScheduler(
                    store,
                    CronSchedulerConfig(check_interval_s=60.0),
                )

                async def _on_cron_fire(task: Any) -> None:
                    try:
                        from .kairos.sleep import wake_agent

                        await wake_agent("cron", task.prompt, task_id=getattr(task, "id", ""))
                    except Exception:
                        logger.debug("Kairos cron wake failed", exc_info=True)

                await scheduler.start(_on_cron_fire)
                self._kairos_cron_scheduler = scheduler
        except Exception:
            logger.debug("Failed to start Kairos runtime", exc_info=True)

    async def _stop_kairos_runtime(self) -> None:
        """Stop Kairos runtime loops started for this agent."""
        proactive_stop = self._kairos_proactive_stop
        if proactive_stop is not None:
            proactive_stop.set()
        proactive_task = self._kairos_proactive_task
        if proactive_task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await proactive_task
        self._kairos_proactive_task = None
        command_task = self._kairos_command_task
        if command_task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await command_task
        self._kairos_command_task = None
        self._kairos_proactive_stop = None

        cron_scheduler = self._kairos_cron_scheduler
        if cron_scheduler is not None:
            try:
                cron_scheduler.stop()
                await cron_scheduler.wait()
            except Exception:
                logger.debug("Failed to stop Kairos cron scheduler", exc_info=True)
        self._kairos_cron_scheduler = None

    async def submit_tool_results(
        self,
        run_id: str,
        results: list[ToolResultBlock | HostToolResult | dict[str, Any]],
    ) -> AsyncGenerator[StreamEvent, None]:
        """Submit client-side tool results and continue the query loop.

        Used when query() yielded a PendingToolCallEvent. Matches the main
        ``query()`` path: mailbox ingest, background notifications, busy state,
        and prevent-sleep for the continuation.
        """
        normalized_results = self._normalize_tool_result_blocks(results)
        for note in self._ingest_session_mailbox_messages():
            yield self._annotate_event(note)
        for note in self._ingest_team_mailbox_messages():
            yield self._annotate_event(note)
        for note in self._yield_background_notifications():
            yield self._annotate_event(note)
        for note in self._yield_runtime_notifications():
            yield self._annotate_event(note)
        self._record_user_activity()

        self._is_processing = True
        self._update_peer_status("busy")
        start_prevent_sleep()

        try:
            engine = QueryEngine(self)
            async for event in engine.continue_with_tool_results(run_id, normalized_results):
                if isinstance(event, CompletionEvent):
                    self._last_reply = event.text
                yield self._annotate_event(event)
        finally:
            stop_prevent_sleep()
            self._update_peer_status("idle")
            self._is_processing = False

        current_turn = getattr(self, "_current_turn_state", None)
        if str(getattr(getattr(current_turn, "phase", None), "value", getattr(current_turn, "phase", ""))) in {"completed", "failed"}:
            self._current_turn_state = None

        for note in self._yield_background_notifications():
            yield self._annotate_event(note)
        for note in self._yield_runtime_notifications():
            yield self._annotate_event(note)

    # ------------------------------------------------------------------
    # Idle loop (resident mode)
    # ------------------------------------------------------------------

    async def _idle_loop(self) -> None:
        """Periodically fire idle hooks when not processing messages."""
        min_interval = min(
            (h.interval for h in self._idle_hooks),
            default=15.0,
        )
        while self._running:
            try:
                await asyncio.sleep(min_interval)
            except asyncio.CancelledError:
                return

            if self._is_processing:
                continue

            due_tasks = self._scheduler.get_due_tasks()
            for task in due_tasks:
                task.mark_run()
                self.schedule_background(self._run_scheduled_task(task))

            for hook in self._idle_hooks:
                try:
                    action = await hook.on_idle(self)
                    if action is not None:
                        await self._execute_idle_action(action)
                except Exception:
                    logger.debug("IdleHook error", exc_info=True)

    async def _run_scheduled_task(self, task: ScheduledTask) -> None:
        """Execute a due scheduled task via background agent (isolated context)."""
        try:
            self._bg_runner.spawn(
                name=f"scheduled-{task.task_id}",
                prompt=task.prompt,
            )
            logger.debug("Scheduled task %s spawned as background agent", task.name)
        except Exception:
            logger.debug("Scheduled task %s failed to spawn", task.name, exc_info=True)

    async def _execute_idle_action(self, action: IdleAction) -> None:
        """Execute an idle action and put it on the queue for external consumers."""
        await self._idle_event_queue.put(action)
        if action.tool_name and action.tool_name in {t.name for t in self._tools}:
            tool = next(t for t in self._tools if t.name == action.tool_name)
            try:
                context = ToolUseContext(
                    conversation_id=self._conversation_id,
                    agent_id=self._agent_id,
                    turn_id=f"idle-{uuid4().hex[:8]}",
                )
                await tool.execute(context=context, **action.tool_args)
            except Exception:
                logger.debug("Idle action tool %s failed", action.tool_name, exc_info=True)

    async def get_idle_action(self, timeout: float = 0.0) -> IdleAction | None:
        """Pop the next idle action from the queue, or None if empty/timeout."""
        try:
            if timeout > 0:
                return await asyncio.wait_for(self._idle_event_queue.get(), timeout)
            return self._idle_event_queue.get_nowait()
        except (asyncio.QueueEmpty, asyncio.TimeoutError):
            return None

    # ------------------------------------------------------------------
    # Background task helpers
    # ------------------------------------------------------------------

    def _record_user_activity(self) -> None:
        """Record that the user re-engaged with the session."""
        self._last_user_activity_at = time.time()
        manager = getattr(self, "_away_summary_manager", None)
        if manager is None:
            return
        try:
            manager.mark_activity()
        except Exception:
            logger.debug("Failed to record away-summary activity", exc_info=True)

    async def _consume_away_summary_notifications(self) -> list[TextEvent]:
        """Generate a return-from-idle summary before the next user turn."""
        events: list[TextEvent] = []
        manager = getattr(self, "_away_summary_manager", None)
        summary: str | None = None
        if manager is not None:
            try:
                if self._messages and manager.should_show(self._messages):
                    summary = await manager.generate(
                        self._messages,
                        session_memory=self._get_session_memory_content(),
                    )
            except Exception:
                logger.debug("Failed to generate away summary", exc_info=True)
        self._record_user_activity()
        if summary:
            self._messages.append(
                system_message(
                    summary,
                    type="system",
                    subtype="away_summary",
                )
            )
            events.append(TextEvent(text=f"[While you were away]\n{summary}"))
        return events

    def _append_runtime_system_message(
        self,
        message: Message,
        *,
        event_text: str | None = None,
    ) -> None:
        """Append a runtime-generated system message and surface it to active hosts."""
        self._messages.append(message)
        self._persist_session_snapshot()

        subtype = str(message.metadata.get("subtype", "") or "")
        if subtype == "memory_saved":
            with contextlib.suppress(Exception):
                self._event_bus.emit(
                    "memory_saved",
                    {
                        "paths": list(message.metadata.get("memory_paths", []) or []),
                        "verb": str(message.metadata.get("verb", "") or ""),
                        "source": str(message.metadata.get("source", "") or ""),
                    },
                )

        text = event_text if event_text is not None else message.text
        if not text:
            return

        event = TextEvent(text=text)
        self._dispatch_runtime_event(event)

    def _yield_runtime_notifications(self) -> list[StreamEvent]:
        """Drain pending runtime notifications created by background hooks."""
        pending = list(self._runtime_notifications)
        self._runtime_notifications.clear()
        return pending

    def _yield_background_notifications(self) -> list[TextEvent]:
        """Drain completed background tasks and format as TextEvents."""
        results = self._bg_runner.drain_completions()
        events: list[TextEvent] = []
        for result in results:
            events.append(TextEvent(text=result.to_notification()))
        return events

    def _ingest_team_mailbox_messages(self) -> list[TextEvent]:
        """Read unread leader-team mailbox messages into history and notifications."""
        try:
            from .delegation.mailbox import FileMailbox
            from .delegation.team_files import TEAM_LEAD_NAME, get_team_mailbox_dir
        except Exception:
            return []

        team_name = str(getattr(getattr(self, "_team_create_tool", None), "_active_team_name", "")).strip()
        if not team_name:
            return []

        try:
            mailbox = FileMailbox(get_team_mailbox_dir(team_name))
            unread = mailbox.read_and_mark(TEAM_LEAD_NAME)
        except Exception:
            logger.debug("Failed to read team mailbox", exc_info=True)
            return []

        events: list[TextEvent] = []
        for msg in unread:
            content = self._normalize_team_mailbox_message(msg)
            if not content:
                continue
            self._messages.append(
                user_message(
                    content,
                    _team_mailbox=True,
                    sender=msg.from_agent,
                    team_name=team_name,
                )
            )
            preview = msg.summary or content.splitlines()[0][:200]
            events.append(TextEvent(text=f"[Teammate message from {msg.from_agent}]\n{preview}"))
        return events

    def _ingest_session_mailbox_messages(self) -> list[TextEvent]:
        """Read unread local cross-session messages into history and notifications."""
        try:
            from .tools.list_peers import read_session_messages
        except Exception:
            return []

        try:
            unread = read_session_messages(self._conversation_id)
        except Exception:
            logger.debug("Failed to read session mailbox", exc_info=True)
            return []

        events: list[TextEvent] = []
        for msg in unread:
            content = self._normalize_team_mailbox_message(msg)
            if not content:
                continue
            self._messages.append(
                user_message(
                    content,
                    _peer_session_mailbox=True,
                    sender=msg.from_agent,
                )
            )
            preview = msg.summary or content.splitlines()[0][:200]
            events.append(TextEvent(text=f"[Session message from {msg.from_agent}]\n{preview}"))
        return events
    @staticmethod
    def _normalize_team_mailbox_message(message: Any) -> str:
        from xml.sax.saxutils import escape

        text = str(getattr(message, "text", "") or "").strip()
        if not text:
            return ""
        if text.startswith("<task-notification>"):
            return text

        try:
            payload = json.loads(text)
        except Exception:
            return text

        if not isinstance(payload, dict) or payload.get("type") != "idle":
            msg_type = str(payload.get("type", "") or "")
            if msg_type == "shutdown_response":
                request_id = str(payload.get("request_id", "") or "")
                approved = bool(payload.get("approve", False))
                if approved:
                    return (
                        "<task-notification>\n"
                        f"<task-id>{escape(request_id or 'shutdown')}</task-id>\n"
                        "<status>completed</status>\n"
                        "<summary>Shutdown approved by teammate</summary>\n"
                        "<result>Teammate approved shutdown and is exiting.</result>\n"
                        "</task-notification>"
                    )
                reason = str(payload.get("reason", "") or "Shutdown rejected")
                return (
                    "<task-notification>\n"
                    f"<task-id>{escape(request_id or 'shutdown')}</task-id>\n"
                    "<status>failed</status>\n"
                    f"<summary>{escape(reason)}</summary>\n"
                    f"<reason>{escape(reason)}</reason>\n"
                    "</task-notification>"
                )
            if msg_type == "plan_approval_response":
                request_id = str(payload.get("request_id", "") or "plan")
                if payload.get("approve", False):
                    return (
                        "<task-notification>\n"
                        f"<task-id>{escape(request_id)}</task-id>\n"
                        "<status>completed</status>\n"
                        "<summary>Plan approved</summary>\n"
                        "<result>Leader approved the proposed plan.</result>\n"
                        "</task-notification>"
                    )
                feedback = str(payload.get("feedback", "") or "Plan rejected")
                return (
                    "<task-notification>\n"
                    f"<task-id>{escape(request_id)}</task-id>\n"
                    "<status>failed</status>\n"
                    f"<summary>{escape(feedback)}</summary>\n"
                    f"<reason>{escape(feedback)}</reason>\n"
                    "</task-notification>"
                )
            if msg_type == "plan_approval_request":
                request_id = str(payload.get("request_id", "") or "plan")
                plan_text = str(payload.get("planContent", "") or "").strip()
                return (
                    "<task-notification>\n"
                    f"<task-id>{escape(request_id)}</task-id>\n"
                    "<status>completed</status>\n"
                    "<summary>Plan approval requested</summary>\n"
                    f"<result>{escape(plan_text or 'Worker requested plan approval.')}</result>\n"
                    "</task-notification>"
                )
            return text

        status = "completed"
        task_status = str(payload.get("task_status", "") or "")
        reason = str(payload.get("reason", "") or "")
        if task_status == "failed" or reason == "failed":
            status = "failed"
        elif reason == "interrupted":
            status = "killed"

        summary = str(payload.get("summary", "") or "") or f'Task "{payload.get("agent", "worker")}" {status}'
        result = str(payload.get("summary", "") or "")
        failure = str(payload.get("failure", "") or "")
        task_id = str(payload.get("task_id", "") or payload.get("agent", "worker"))

        parts = [
            "<task-notification>",
            f"<task-id>{escape(task_id)}</task-id>",
            f"<status>{status}</status>",
            f"<summary>{escape(summary)}</summary>",
        ]
        if result:
            parts.append(f"<result>{escape(result)}</result>")
        if failure and status != "killed":
            parts.append(f"<reason>{escape(failure)}</reason>")
        parts.append("</task-notification>")
        return "\n".join(parts)

    def schedule_background(self, coro: Any) -> asyncio.Task[Any]:
        """Schedule a coroutine as a managed background task."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    # ------------------------------------------------------------------
    # Event bus (decouple subsystem coordination)
    # ------------------------------------------------------------------

    @property
    def event_bus(self) -> EventBus:
        """Internal event bus for cross-subsystem coordination."""
        return self._event_bus

    # ------------------------------------------------------------------
    # Conversation recovery
    # ------------------------------------------------------------------

    async def _attempt_recovery(self) -> bool:
        """Check for and restore recovery state from a previous crash.

        Returns True if state was recovered.
        """
        recovery = ConversationRecovery(agent_id=self._agent_id)
        if not recovery.has_recovery_state():
            return False

        try:
            state = recovery.load_recovery_state()
            if state is None:
                return False

            self._messages = state["messages"]
            if state.get("system_prompt_text"):
                self._system_prompt.add_static(
                    state["system_prompt_text"],
                    key="recovered_prompt",
                )
            logger.info(
                "Recovered %d messages from previous session",
                len(self._messages),
            )
            recovery.cleanup()
            self._event_bus.emit("recovery_complete", {
                "message_count": len(self._messages),
            })
            return True
        except Exception:
            logger.debug("Recovery failed, starting fresh", exc_info=True)
            recovery.cleanup()
            return False

    def save_recovery_state(self) -> Path | None:
        """Save current conversation state for crash recovery."""
        recovery = ConversationRecovery(agent_id=self._agent_id)
        return recovery.save_recovery_state(
            messages=self._messages,
            system_prompt_text=self._system_prompt.render(),
        )

    # ------------------------------------------------------------------
    # Message re-injection after compaction
    # ------------------------------------------------------------------

    def _collect_reinjection_context(self) -> list[dict[str, str]]:
        """Gather important context that should survive compaction.

        Looks for plan references, skill context, and recently-read file
        content in the conversation history.
        """
        contexts: list[dict[str, str]] = []

        for msg in self._messages:
            role = msg.role if hasattr(msg, "role") else msg.get("role", "")
            if role != "user":
                continue
            content = msg.content if hasattr(msg, "content") else msg.get("content", "")
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                for b in content:
                    if hasattr(b, "text"):
                        text += " " + b.text
                    elif isinstance(b, dict):
                        text += " " + b.get("text", "")

            lower = text.lower()
            if "plan.md" in lower or "## plan" in lower:
                contexts.append({"type": "plan", "content": text[:2000]})
            elif "skill" in lower and ("read" in lower or "follow" in lower):
                contexts.append({"type": "skill", "content": text[:2000]})

        return contexts[-5:]

    def _reinject_messages(self) -> None:
        """Re-inject important context messages after compaction.

        Called automatically after compact to preserve plan context,
        skill instructions, and other critical information.
        """
        contexts = self._collect_reinjection_context()
        if not contexts:
            return

        parts: list[str] = []
        for ctx in contexts:
            parts.append(f"[Re-injected {ctx['type']} context]\n{ctx['content']}")

        if parts:
            injection = user_message(
                "<system-reminder>\n"
                + "\n\n---\n\n".join(parts)
                + "\n</system-reminder>"
            )
            self._messages.append(injection)
            logger.debug("Re-injected %d context blocks after compact", len(parts))
            self._event_bus.emit("reinjection_complete", {"count": len(parts)})

    # ------------------------------------------------------------------
    # Agent summary for background task visibility
    # ------------------------------------------------------------------

    def _start_agent_summary(self) -> None:
        """Start periodic 3-5 word summary generation for this agent."""
        if self._summary_tracker is not None:
            return
        from .delegation.subagent import AgentSummaryTracker

        self._summary_tracker = AgentSummaryTracker(provider=self._provider)
        self._summary_tracker.start(lambda: self._messages)
        logger.debug("Agent summary tracker started")

    def _stop_agent_summary(self) -> None:
        """Stop the periodic summary tracker."""
        if self._summary_tracker is not None:
            self._summary_tracker.stop()
            self._summary_tracker = None

    def get_current_summary(self) -> str:
        """Return the latest 3-5 word progress summary, or empty string."""
        if self._summary_tracker is None:
            return ""
        return self._summary_tracker.get_summary()

    async def inject_channel_notification(
        self,
        *,
        channel_tag: str,
        content: str,
        sender: str = "",
        metadata: dict[str, Any] | None = None,
        server_name: str = "",
    ) -> bool:
        """Inject an external channel notification into the Kairos wake path."""
        from .kairos.channels import (
            ChannelEntry,
            ChannelKind,
            ChannelNotification,
            get_channel_registry,
        )

        registry = get_channel_registry()
        channel = registry.get(channel_tag)
        if channel is None:
            kind_text, _, name = channel_tag.partition(":")
            if kind_text == ChannelKind.PLUGIN.value and name:
                kind = ChannelKind.PLUGIN
            else:
                kind = ChannelKind.SERVER
                name = name or channel_tag
            channel = ChannelEntry(
                kind=kind,
                name=name,
                server_name=server_name,
            )
            registry.register(channel)

        notification = ChannelNotification(
            channel=channel,
            content=content,
            sender=sender,
            metadata=dict(metadata or {}),
        )
        return await registry.handle_notification(notification)

    def _register_peer_session(self) -> None:
        """Publish this session so peer discovery can find it."""
        try:
            from .tools.list_peers import register_session

            register_session(
                self._conversation_id,
                working_dir=self.working_directory,
                model=self._provider.model_name,
                agent_id=self._agent_id,
            )
        except Exception:
            logger.debug("Failed to register peer session", exc_info=True)

    def _update_peer_status(self, status: str) -> None:
        """Keep peer discovery metadata in sync with current activity."""
        try:
            from .tools.list_peers import update_session_status

            update_session_status(self._conversation_id, status)
        except Exception:
            logger.debug("Failed to update peer status", exc_info=True)

    def _unregister_peer_session(self) -> None:
        """Remove this session from the peer registry on shutdown."""
        try:
            from .tools.list_peers import unregister_session

            unregister_session(self._conversation_id)
        except Exception:
            logger.debug("Failed to unregister peer session", exc_info=True)

    def _restore_plan_mode_state(self) -> None:
        """Restore any persisted plan-mode state for this session."""
        try:
            from .tools.plan_mode import is_plan_mode_active, restore_plan_mode

            restore_plan_mode()
            if is_plan_mode_active() and self._permission_checker is not None:
                from .permissions import PermissionMode

                self._permission_checker.set_mode(PermissionMode.PLAN)
        except Exception:
            logger.debug("Failed to restore plan mode state", exc_info=True)

    def _restore_coordinator_mode_state(self) -> None:
        """Restore coordinator mode from disk (embedded SDK; mirrors CLI coordinator init).

        Syncs ``CLAUDE_CODE_COORDINATOR_MODE`` via ``CoordinatorMode.restore_mode`` and
        activates the coordinator object when the persisted session was coordinator.
        """
        if self._mode_overridden_by_host:
            return
        try:
            from .delegation.coordinator import CoordinatorMode, is_coordinator_mode

            if getattr(self, "_coordinator_mode", None) is None:
                self._coordinator_mode = CoordinatorMode()
            coord = self._coordinator_mode
            notice = coord.restore_mode(os.getcwd())
            if notice:
                logger.info("%s", notice)
            if is_coordinator_mode() and not coord.is_active:
                coordinator_tools = [tool.name for tool in self._tools]
                worker_tools: list[str] = []
                try:
                    from .delegation.multi_agent import AgentTool

                    for tool in self._tools:
                        if isinstance(tool, AgentTool):
                            worker_tools = [
                                child.name for child in getattr(tool, "_parent_tools", [])
                            ]
                            break
                except Exception:
                    logger.debug("Failed to inspect worker tools for coordinator context", exc_info=True)
                coord.activate(
                    coordinator_tool_names=coordinator_tools or None,
                    worker_tool_names=worker_tools or None,
                    sync_env=False,
                )
        except Exception:
            logger.debug("Failed to restore coordinator mode state", exc_info=True)

    def _save_coordinator_mode_state(self) -> None:
        """Persist coordinator-mode state for future session resumes."""
        coordinator = getattr(self, "_coordinator_mode", None)
        if coordinator is None:
            return
        try:
            coordinator.save_mode(os.getcwd())
        except Exception:
            logger.debug("Failed to persist coordinator mode state", exc_info=True)

    # ------------------------------------------------------------------
    # Graceful shutdown (mirrors Claude Code's gracefulShutdown.ts)
    # ------------------------------------------------------------------

    async def _graceful_shutdown(self, *, save_recovery: bool = False) -> None:
        """Drain pending work and cleanly shut down all subsystems.

        Called from ``__aexit__`` and on SIGINT/SIGTERM.

        Steps:
        1. Stop summary tracker
        2. Drain pending memory/session extractions (via hooks)
        3. Cancel background tasks
        4. Save recovery state if requested
        5. Fire shutdown event
        """
        if self._shutdown_in_progress:
            return
        self._shutdown_in_progress = True

        logger.debug("Graceful shutdown initiated")
        self._event_bus.emit("shutdown_start", {})

        self._stop_agent_summary()

        try:
            await self._hook_runner.run_session_end(agent=self)
        except Exception:
            logger.debug("Session-end hooks error during shutdown", exc_info=True)

        await self._bg_runner.cancel_all()

        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        if save_recovery and self._messages:
            self.save_recovery_state()

        self._event_bus.emit("shutdown_complete", {})
        self._shutdown_in_progress = False

    def _install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers that trigger graceful shutdown."""
        loop = asyncio.get_running_loop()

        def _signal_handler(sig: int) -> None:
            logger.info("Received signal %s, initiating graceful shutdown", sig)
            asyncio.ensure_future(
                self._graceful_shutdown(save_recovery=True)
            )

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler, sig)
                self._installed_signals.append(sig)
            except (NotImplementedError, OSError):
                pass

    def _remove_signal_handlers(self) -> None:
        """Remove signal handlers installed by this agent."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for sig in self._installed_signals:
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, OSError):
                pass
        self._installed_signals.clear()


# ======================================================================
# Event bus — lightweight pub/sub for internal coordination
# ======================================================================

class EventBus:
    """Simple async event bus for decoupled subsystem coordination.

    Mirrors the event-driven patterns in Claude Code where subsystems
    communicate via events rather than direct method calls.

    Supported events::

        "tool_complete"      — a tool finished execution
        "compact_done"       — context compaction completed
        "memory_saved"       — memory extraction finished
        "turn_end"           — a query turn completed
        "recovery_complete"  — crash recovery restored state
        "reinjection_complete" — post-compact context re-injection done
        "shutdown_start"     — graceful shutdown began
        "shutdown_complete"  — graceful shutdown finished

    Usage::

        bus = EventBus()
        bus.on("tool_complete", lambda data: print(data))
        bus.emit("tool_complete", {"tool": "grep", "duration": 1.2})
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable[[dict[str, Any]], Any]]] = {}

    def on(
        self,
        event_type: str,
        callback: Callable[[dict[str, Any]], Any],
    ) -> Callable[[], None]:
        """Register a callback for an event type. Returns unsubscribe fn."""
        self._listeners.setdefault(event_type, []).append(callback)

        def unsubscribe() -> None:
            try:
                self._listeners[event_type].remove(callback)
            except (ValueError, KeyError):
                pass

        return unsubscribe

    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Fire an event, calling all registered listeners."""
        for cb in self._listeners.get(event_type, []):
            try:
                result = cb(data)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception:
                logger.debug(
                    "EventBus listener error for %s", event_type, exc_info=True,
                )

    def clear(self, event_type: str | None = None) -> None:
        """Remove listeners. If event_type is None, remove all."""
        if event_type is None:
            self._listeners.clear()
        else:
            self._listeners.pop(event_type, None)


# ======================================================================
# Conversation recovery — save/restore across crashes
# ======================================================================

class ConversationRecovery:
    """Persist and restore conversation state for crash recovery.

    Mirrors Claude Code's session persistence / resume patterns from
    ``sessionStorage.ts`` and ``gracefulShutdown.ts``.
    """

    def __init__(
        self,
        *,
        agent_id: str = "agent",
        base_dir: Path | None = None,
    ) -> None:
        self._agent_id = agent_id
        if base_dir is None:
            self._dir = mini_agent_home() / "recovery"
        else:
            self._dir = base_dir

    @property
    def _state_path(self) -> Path:
        return self._dir / f"{self._agent_id}_recovery.json"

    def has_recovery_state(self) -> bool:
        """Check if recovery state exists from a previous crash."""
        return self._state_path.exists()

    def save_recovery_state(
        self,
        *,
        messages: list[Any],
        system_prompt_text: str = "",
    ) -> Path | None:
        """Serialize messages + system prompt to disk."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            state = {
                "agent_id": self._agent_id,
                "timestamp": time.time(),
                "system_prompt_text": system_prompt_text,
                "messages": _serialize_messages(messages),
            }
            self._state_path.write_text(
                json.dumps(state, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            logger.debug("Saved recovery state to %s", self._state_path)
            return self._state_path
        except Exception:
            logger.debug("Failed to save recovery state", exc_info=True)
            return None

    def load_recovery_state(self) -> dict[str, Any] | None:
        """Deserialize and return saved state, or None."""
        try:
            raw = self._state_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            max_age_hours = 24
            if time.time() - data.get("timestamp", 0) > max_age_hours * 3600:
                logger.debug("Recovery state too old, discarding")
                return None
            raw_messages = data.get("messages", [])
            if isinstance(raw_messages, list):
                data["messages"] = _deserialize_recovery_messages(raw_messages)
            else:
                data["messages"] = []
            return data
        except Exception:
            logger.debug("Failed to load recovery state", exc_info=True)
            return None

    def cleanup(self) -> None:
        """Remove recovery state after successful restore."""
        try:
            self._state_path.unlink(missing_ok=True)
        except Exception:
            pass


def _serialize_messages(messages: list[Any]) -> list[Any]:
    """Best-effort JSON-safe serialization of Message dataclasses or dicts."""
    result = []
    for msg in messages:
        if isinstance(msg, dict):
            result.append(msg)
        elif hasattr(msg, "role") and hasattr(msg, "content"):
            content = msg.content
            if isinstance(content, str):
                serialized_content = content
            elif isinstance(content, list):
                serialized_content = [
                    b.__dict__ if hasattr(b, "__dict__") else str(b)
                    for b in content
                ]
            else:
                serialized_content = str(content)
            entry: dict[str, Any] = {
                "role": msg.role,
                "content": serialized_content,
            }
            if hasattr(msg, "name") and msg.name:
                entry["name"] = msg.name
            result.append(entry)
        elif hasattr(msg, "__dict__"):
            result.append(msg.__dict__)
        else:
            result.append({"content": str(msg)})
    return result


def _deserialize_recovery_messages(messages: list[Any]) -> list[Message]:
    """Best-effort restore of saved recovery messages into Message objects."""
    restored: list[Message] = []
    for item in messages:
        if isinstance(item, Message):
            restored.append(item)
            continue
        if isinstance(item, dict):
            try:
                restored.append(_dict_to_message(item))
                continue
            except Exception:
                logger.debug("Failed to deserialize recovery message", exc_info=True)
        restored.append(Message(role="user", content=str(item), metadata={"isMeta": True}))
    return restored
