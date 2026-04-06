"""Built-in tools for mini-agent.

Mirrors Claude Code's core tool set:
- File operations: read, write, edit
- Directory operations: list, glob, grep
- Shell execution: bash (with streaming output)
- Web: fetch URL contents
- Background: spawn / monitor background agent tasks
"""

from .lsp_tool import LSPTool
from dataclasses import dataclass

from ..delegation.background import BackgroundAgentRunner
from ..delegation.multi_agent import AgentTool
from ..kairos import TaskStore
from ..kairos.core import GateConfig
from ..profiles import RuntimeProfile
from ..providers import BaseProvider
from ..tool import Tool
from .mcp_resources import ListMCPResourcesTool, ReadMCPResourceTool
from .notebook_edit import NotebookEditTool
from .powershell import PowerShellTool
from .repl import REPLTool
from .config_tool import ConfigTool
from .remote_trigger import RemoteTriggerTool
from .schedule_cron import CronCreateTool, CronDeleteTool, CronListTool
from .send_user_message import SendUserMessageTool
from .skill_tool import SkillTool
from .synthetic_output import SyntheticOutputTool, create_synthetic_output_tool
from .task_tools import (
    TaskBoard,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskOutputTool,
    TaskUpdateTool,
)
from .worktree import EnterWorktreeTool, ExitWorktreeTool
from .workflow import WorkflowTool
from .ask_user import AskUserQuestionTool
from .bash import BashTool
from .file_edit import FileEditTool
from .file_read import FileReadTool
from .file_write import FileWriteTool
from .glob_tool import GlobTool
from .grep_tool import GrepTool
from .send_message import SendMessageTool
from .sleep_tool import SleepTool
from .team import TaskStopTool, TeamCreateTool, TeamDeleteTool
from .todo_write import TodoWriteTool
from .web_fetch import WebFetchTool
from .web_search import WebSearchTool
from .tool_search import (
    ToolSearchConfig,
    ToolSearchTool,
    format_deferred_tool_list,
    is_deferred_tool,
    partition_tools,
    search_tools,
)
from .plan_mode import (
    EnterPlanModeTool,
    ExitPlanModeTool,
    PlanState,
    VerifyPlanExecutionTool,
    get_plan_state,
    is_plan_mode_active,
    reset_plan_state,
)
from .list_peers import (
    ListPeersTool,
    PeerInfo,
    count_live_sessions,
    list_live_peers,
    register_session,
    unregister_session,
    update_session_name,
    update_session_status,
)
from .push_notification import PushNotificationTool
from .send_user_file import SendUserFileTool
from .subscribe_pr import SubscribePRTool

try:
    from .mcp_auth import McpAuthTool
except ModuleNotFoundError:
    McpAuthTool = None  # type: ignore[assignment]

__all__ = [
    "AskUserQuestionTool",
    "BashTool",
    "ConfigTool",
    "create_synthetic_output_tool",
    "CronCreateTool",
    "CronDeleteTool",
    "CronListTool",
    "EnterPlanModeTool",
    "EnterWorktreeTool",
    "ExitPlanModeTool",
    "ExitWorktreeTool",
    "FileEditTool",
    "FileReadTool",
    "FileWriteTool",
    "GlobTool",
    "GrepTool",
    "ListMCPResourcesTool",
    "LSPTool",
    "NotebookEditTool",
    "PlanState",
    "PowerShellTool",
    "REPLTool",
    "ReadMCPResourceTool",
    "RemoteTriggerTool",
    "SendUserMessageTool",
    "SendMessageTool",
    "SkillTool",
    "SleepTool",
    "SyntheticOutputTool",
    "TaskBoard",
    "TaskCreateTool",
    "TaskGetTool",
    "TaskListTool",
    "TaskOutputTool",
    "TaskStopTool",
    "TaskUpdateTool",
    "TeamCreateTool",
    "TeamDeleteTool",
    "TodoWriteTool",
    "ToolSearchConfig",
    "ToolSearchTool",
    "VerifyPlanExecutionTool",
    "WebFetchTool",
    "WebSearchTool",
    "WorkflowTool",
    # ListPeers
    "ListPeersTool",
    "PeerInfo",
    "PushNotificationTool",
    "SendUserFileTool",
    "SubscribePRTool",
    "count_live_sessions",
    "list_live_peers",
    "register_session",
    "unregister_session",
    "update_session_name",
    "update_session_status",
    # ToolSearch
    "format_deferred_tool_list",
    "get_plan_state",
    "is_deferred_tool",
    "is_plan_mode_active",
    "partition_tools",
    "reset_plan_state",
    "search_tools",
    "ToolAssembly",
    "build_default_tools",
]

if McpAuthTool is not None:
    __all__.append("McpAuthTool")


def get_default_tools(**kwargs):
    """Create the standard set of built-in tools.

    Keyword arguments are forwarded to tools that accept configuration
    (e.g. ``allowed_dirs`` for file tools, ``timeout`` for bash).

    If ``background_runner`` is provided, background task tools are included.
    """
    from ..delegation.background import BackgroundAgentRunner
    from ..services.web_search_backend import default_web_search

    allowed_dirs = kwargs.get("allowed_dirs")
    tools = [
        FileReadTool(allowed_dirs=allowed_dirs),
        FileWriteTool(allowed_dirs=allowed_dirs),
        FileEditTool(),
        GlobTool(),
        GrepTool(),
        BashTool(
            timeout=kwargs.get("bash_timeout", 120),
            working_dir=kwargs.get("working_dir"),
        ),
        WebFetchTool(),
        WebSearchTool(search_fn=kwargs.get("search_fn") or default_web_search),
        TodoWriteTool(),
    ]

    include_ask_user = kwargs.get("include_ask_user", False)
    if include_ask_user:
        tools.append(AskUserQuestionTool())

    runner: BackgroundAgentRunner | None = kwargs.get("background_runner")
    if runner is not None:
        tools.append(SendMessageTool(runner))

    return tools


def _dedupe_tools(tools: list[Tool]) -> list[Tool]:
    """Keep the first instance for each tool name."""
    unique: list[Tool] = []
    seen: set[str] = set()
    for tool in tools:
        name = str(getattr(tool, "name", "") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        unique.append(tool)
    return unique


def _finalize_tool_runtime(tools: list[Tool]) -> list[Tool]:
    """Update tools that need to see the full assembled pool."""
    finalized = _dedupe_tools(tools)
    loaded, deferred = partition_tools(finalized)
    tool_map = {tool.name: tool for tool in finalized}
    for tool in finalized:
        if isinstance(tool, ToolSearchTool):
            tool.set_tools(finalized, deferred)
        if isinstance(tool, WorkflowTool):
            tool.set_tool_map(tool_map)
    return finalized


@dataclass(slots=True)
class ToolAssembly:
    """Resolved default tools plus any host-visible helper handles."""

    tools: list[Tool]
    team_create_tool: TeamCreateTool | None = None


def _kairos_extra_tools(gate: GateConfig | None) -> list[Tool]:
    """Tools gated on KAIROS (mirrors Claude Code ``tools.ts`` conditional block)."""
    env = GateConfig.from_env()
    if gate is None:
        enabled = env.kairos_enabled
    else:
        enabled = bool(gate.kairos_enabled) or env.kairos_enabled
    if not enabled:
        return []
    return [
        SendUserFileTool(),
        PushNotificationTool(),
        SubscribePRTool(),
    ]


def build_default_tools(
    profile: RuntimeProfile | str,
    *,
    provider: BaseProvider,
    background_runner: BackgroundAgentRunner | None = None,
    registry: object | None = None,
    extra_tools: list[Tool] | None = None,
    kairos_gate: GateConfig | None = None,
) -> ToolAssembly:
    """Build the default tool set for a runtime profile.

    The shared core keeps the same tool implementations for every host.
    Profiles only change which tools are enabled by default.
    """

    RuntimeProfile(profile)
    team_create_tool = TeamCreateTool(provider=provider)
    task_board = TaskBoard()
    cron_store = TaskStore()

    worker_tools = get_default_tools(
        background_runner=None,
        include_ask_user=False,
    )
    worker_tools.extend(list(extra_tools or []))
    worker_tools.extend([
        PowerShellTool(),
        NotebookEditTool(),
        EnterWorktreeTool(),
        ExitWorktreeTool(),
        ToolSearchTool(),
    ])
    if registry is not None:
        worker_tools.append(
            SkillTool(
                provider=provider,
                registry=registry,  # type: ignore[arg-type]
                parent_tools=list(worker_tools),
            )
        )
    worker_tools = _finalize_tool_runtime(worker_tools)

    tools = get_default_tools(
        background_runner=None,
        include_ask_user=True,
    )
    tools.extend(list(extra_tools or []))
    tools.extend([
        ConfigTool(),
        LSPTool(),
        ListMCPResourcesTool(),
        ReadMCPResourceTool(),
        NotebookEditTool(),
        PowerShellTool(),
        REPLTool(),
        RemoteTriggerTool(),
        SendUserMessageTool(),
        SleepTool(),
        SyntheticOutputTool(),
        TaskCreateTool(task_board),
        TaskGetTool(task_board),
        TaskListTool(task_board),
        TaskUpdateTool(task_board),
        CronCreateTool(cron_store),
        CronListTool(cron_store),
        CronDeleteTool(cron_store),
        EnterPlanModeTool(),
        ExitPlanModeTool(),
        VerifyPlanExecutionTool(),
        EnterWorktreeTool(),
        ExitWorktreeTool(),
        ToolSearchTool(),
        WorkflowTool(),
    ])
    if McpAuthTool is not None:
        tools.append(McpAuthTool())
    tools.extend(_kairos_extra_tools(kairos_gate))

    if background_runner is not None:
        tools.append(
            SendMessageTool(
                background_runner,
                team_create_tool=team_create_tool,
            )
        )
        tools.append(
            TaskStopTool(
                background_runner=background_runner,
                team_create_tool=team_create_tool,
            )
        )
        tools.append(TaskOutputTool(background_runner))

    tools.append(team_create_tool)
    tools.append(ListPeersTool())
    tools.append(TeamDeleteTool(team_create_tool))
    if registry is not None:
        tools.append(
            SkillTool(
                provider=provider,
                registry=registry,  # type: ignore[arg-type]
                parent_tools=list(tools),
            )
        )

    # AgentTool must see the already-assembled tool pool so spawned workers
    # inherit the host's currently enabled coordination tools.
    tools.insert(0, AgentTool(provider=provider, parent_tools=list(worker_tools)))
    tools = _finalize_tool_runtime(tools)

    return ToolAssembly(
        tools=tools,
        team_create_tool=team_create_tool,
    )
