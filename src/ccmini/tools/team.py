"""Team management tools — create, delete teams and stop workers.

Mirrors Claude Code's TeamCreateTool, TeamDeleteTool, and TaskStopTool.
These tools are only available in coordinator mode.
"""

from __future__ import annotations

import json
import os
from uuid import uuid4
from typing import Any

from ..delegation.coordinator import CoordinatorMode
from ..delegation.mailbox import FileMailbox, MailboxMessage
from ..delegation.teammate import (
    SharedTaskList,
    Team,
    TeamConfig,
    TeammateDiscovery,
)
from ..delegation.team_files import (
    TEAM_LEAD_NAME,
    cleanup_team_directories,
    clear_leader_team_name,
    ensure_team_directories,
    ensure_team_file,
    get_leader_team_name,
    get_task_board_path,
    get_team_file_path,
    get_team_mailbox_dir,
    read_team_file,
    sanitize_name,
    set_leader_team_name,
)
from ..providers import BaseProvider
from ..tool import Tool, ToolUseContext
from ..paths import mini_agent_path


class TeamCreateTool(Tool):
    """Create a new team of persistent workers."""

    name = "TeamCreate"
    description = "Create a new agent team for coordinating multiple workers."
    instructions = """\
Create a new team for orchestrating multiple persistent workers.

Use this when you need to:
- Run a large task with multiple parallel workers
- Set up a research + implementation pipeline
- Coordinate workers that stay alive between tasks

Each team has:
- A shared mailbox for inter-worker communication
- A shared task list for auto-claiming work
- A team lead (you, the coordinator)

After creating a team, spawn workers using the agent tool with \
subagent_type="worker".\
"""
    is_read_only = False

    def __init__(
        self,
        *,
        provider: BaseProvider,
        coordinator: CoordinatorMode | None = None,
    ) -> None:
        self._provider = provider
        self._coordinator = coordinator
        self._teams: dict[str, Team] = {}
        self._active_team_name: str = get_leader_team_name()
        if self._active_team_name and read_team_file(self._active_team_name) is None:
            clear_leader_team_name()
            self._active_team_name = ""
        elif self._active_team_name:
            self._restore_team(self._active_team_name)

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "team_name": {
                    "type": "string",
                    "description": "Name for the new team.",
                },
                "description": {
                    "type": "string",
                    "description": "Team description / purpose.",
                },
                "agent_type": {
                    "type": "string",
                    "description": "Optional type/role for the team lead.",
                },
            },
            "required": ["team_name"],
        }

    def _generate_unique_team_name(self, requested: str) -> str:
        base = sanitize_name(requested)
        if not base:
            base = "team"
        candidate = base
        suffix = 2
        while candidate in self._teams or read_team_file(candidate) is not None:
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def _restore_team(self, team_name: str) -> Team | None:
        team_name = sanitize_name(team_name)
        persisted = read_team_file(team_name)
        if persisted is None:
            return None
        discovery = TeammateDiscovery()
        live_peer_ids = {peer.peer_id for peer in discovery.discover_peers(include_self=True)}
        members = persisted.get("members", [])
        changed = False
        if isinstance(members, list):
            for member in members:
                if not isinstance(member, dict):
                    continue
                member_id = str(member.get("agentId", "")).strip()
                member_name = str(member.get("name", "")).strip()
                if member_name == TEAM_LEAD_NAME:
                    continue
                live = member_id in live_peer_ids
                if member.get("isActive", True) != live:
                    member["isActive"] = live
                    changed = True
        if changed:
            from ..delegation.team_files import write_team_file

            write_team_file(team_name, persisted)
        task_list = SharedTaskList()
        try:
            from .task_tools import TaskBoard

            board = TaskBoard(path=get_task_board_path(team_name))
            for record in board.list():
                task_list.upsert(
                    task_id=record.id,
                    subject=record.subject,
                    description=record.description,
                    status=record.status,
                    owner=record.owner or "",
                    blocked_by=list(record.blockedBy),
                )
        except Exception:
            pass
        team = Team(
            provider=self._provider,
            config=TeamConfig(
                team_name=str(persisted.get("name", team_name)).strip() or team_name,
                description=str(persisted.get("description", "")).strip(),
            ),
            mailbox=FileMailbox(get_team_mailbox_dir(team_name)),
            task_list=task_list,
        )
        self._teams[team_name] = team
        return team

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        requested_team_name: str = kwargs["team_name"]
        description: str = kwargs.get("description", "")
        agent_type: str = str(kwargs.get("agent_type", "")).strip() or TEAM_LEAD_NAME

        if self._active_team_name:
            return (
                f'Already leading team "{self._active_team_name}". '
                "Delete the current team before creating a new one."
            )
        team_name = self._generate_unique_team_name(requested_team_name)

        team = Team(
            provider=self._provider,
            config=TeamConfig(team_name=team_name, description=description),
            mailbox=FileMailbox(get_team_mailbox_dir(team_name)),
            task_list=SharedTaskList(),
        )
        self._teams[team_name] = team
        self._active_team_name = team_name
        ensure_team_directories(team_name)
        lead_agent_id = f"{TEAM_LEAD_NAME}@{team_name}"
        ensure_team_file(
            team_name,
            description=description,
            lead_agent_id=lead_agent_id,
            lead_session_id=context.conversation_id,
            lead_agent_type=agent_type,
            model=getattr(self._provider, "model_name", ""),
            cwd=os.getcwd(),
        )
        set_leader_team_name(team_name)

        return (
            "{\n"
            f'  "team_name": "{team_name}",\n'
            f'  "team_file_path": "{get_team_file_path(team_name)}",\n'
            f'  "lead_agent_id": "team-lead@{team_name}",\n'
            '  "success": true\n'
            "}"
        )

    def get_team(self, team_name: str) -> Team | None:
        team_name = sanitize_name(team_name)
        team = self._teams.get(team_name)
        if team is not None:
            return team
        return self._restore_team(team_name)

    def list_teams(self) -> list[str]:
        names = set(self._teams.keys())
        if self._active_team_name:
            names.add(self._active_team_name)
        teams_root = mini_agent_path("teams")
        if teams_root.exists():
            for path in teams_root.iterdir():
                if path.is_dir():
                    names.add(path.name)
        return sorted(names)


class TeamDeleteTool(Tool):
    """Delete a team and shut down all its workers."""

    name = "TeamDelete"
    description = "Delete a team, shutting down all its workers."
    instructions = """\
Delete a team and gracefully shut down all its workers.

Use this when:
- The task is complete and workers are no longer needed
- You want to free up resources
- Starting fresh with a different team configuration

Workers receive a shutdown signal and have a brief window to \
finish their current work before being terminated.\
"""
    is_read_only = False

    def __init__(self, team_create_tool: TeamCreateTool) -> None:
        self._team_create = team_create_tool

    def get_parameters_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "team_name": {
                    "type": "string",
                    "description": "Optional explicit team name. Defaults to the current active team.",
                },
            },
        }
        return schema

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        team_name = sanitize_name(str(kwargs.get("team_name", "")).strip())
        if not team_name:
            if self._team_create._active_team_name:
                team_name = self._team_create._active_team_name
            else:
                teams = self._team_create.list_teams()
                if len(teams) == 1:
                    team_name = teams[0]

        if not team_name:
            return "No active team found."

        team = self._team_create.get_team(team_name)
        team_file = read_team_file(team_name)
        if team_file is None and team is None:
            return f"No team found with name: {team_name}"

        members = team_file.get("members", []) if isinstance(team_file, dict) else []
        persisted_member_count = len(
            [
                member
                for member in members
                if isinstance(member, dict) and str(member.get("name", "")) != TEAM_LEAD_NAME
            ]
        )
        teammates = team.list_teammates() if team is not None else []
        if team is not None:
            await team.shutdown_all()
            self._team_create._teams.pop(team_name, None)
        if self._team_create._active_team_name == team_name:
            self._team_create._active_team_name = ""
            clear_leader_team_name()
        cleanup_team_directories(team_name)

        return (
            f"Team '{team_name}' deleted. "
            f"{max(len(teammates), persisted_member_count)} worker(s) shut down."
        )


class TaskStopTool(Tool):
    """Stop a running worker/background task."""

    name = "TaskStop"
    aliases = ("KillShell",)
    description = "Stop a running worker or background task."
    instructions = """\
Stop a running worker that you sent in the wrong direction.

Use this when:
- You realize the approach is wrong mid-flight
- The user changes requirements after you launched the worker
- A worker is stuck or taking too long

The worker receives a stop signal and can be continued later \
with SendMessage if needed.

Pass the task_id from the Agent tool's launch result.\
"""
    is_read_only = False

    def __init__(
        self,
        *,
        background_runner: Any = None,
        team_create_tool: TeamCreateTool | None = None,
    ) -> None:
        self._runner = background_runner
        self._team_create = team_create_tool

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "ID of the task/worker to stop.",
                },
                "shell_id": {
                    "type": "string",
                    "description": "Deprecated alias for task_id.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for stopping.",
                },
            },
            "required": [],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        task_id: str = str(kwargs.get("task_id") or kwargs.get("shell_id") or "").strip()
        if not task_id:
            return "Missing required parameter: task_id"
        reason: str = kwargs.get("reason", "")

        if self._runner is not None:
            cancelled = self._runner.cancel(task_id)
            if cancelled:
                return f"Task {task_id} stop signal sent."

        if self._team_create is not None:
            for team in self._team_create._teams.values():
                for t in team.list_teammates():
                    if t.identity.agent_id == task_id:
                        name = t.identity.agent_name
                        team.send_shutdown(name, reason)
                        return f"Shutdown sent to worker {name} ({task_id})."

            active_team_name = str(getattr(self._team_create, "_active_team_name", "")).strip()
            if active_team_name:
                team_file = read_team_file(active_team_name)
                if team_file is not None:
                    members = team_file.get("members", [])
                    for member in members if isinstance(members, list) else []:
                        if not isinstance(member, dict):
                            continue
                        member_id = str(member.get("agentId", "")).strip()
                        member_name = str(member.get("name", "")).strip()
                        if task_id not in {member_id, member_name} or member_name == TEAM_LEAD_NAME:
                            continue
                        mailbox = FileMailbox(get_team_mailbox_dir(active_team_name))
                        payload = {
                            "type": "shutdown_request",
                            "request_id": uuid4().hex[:12],
                            "from": TEAM_LEAD_NAME,
                        }
                        if reason:
                            payload["reason"] = reason
                        mailbox.send(
                            member_name,
                            MailboxMessage(
                                from_agent=TEAM_LEAD_NAME,
                                text=json.dumps(payload, ensure_ascii=False),
                                msg_type="shutdown",
                            ),
                        )
                        return f"Shutdown sent to worker {member_name} ({task_id})."

        return f"No running task/worker found with id: {task_id}"
