"""Persistent teammate runner — long-lived agent that polls for work.

Mirrors Claude Code's ``inProcessRunner.ts`` (idle notifications, mailbox
polls, plan/shutdown flows) without React ``AppState``, tmux panes, or
``runWithTeammateContext`` AsyncLocalStorage — those are host/UI concerns.

Unlike background tasks
that execute once and terminate, a teammate:

1. Runs a task to completion
2. Enters **idle** state (does NOT terminate)
3. Polls its mailbox for new messages from the leader or peers
4. Can auto-claim tasks from a shared task list
5. Handles shutdown requests (passes to model for decision)
6. Loops back to step 1

This enables the pattern::

    coordinator: "Research the auth module"
    teammate: (researches, reports, goes idle)
    coordinator: send_message(to=teammate, "Now fix the bug at line 42")
    teammate: (fixes, reports, goes idle)
    coordinator: send_message(to=teammate, shutdown)
    teammate: (shuts down)
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from ..paths import mini_agent_path
from ..messages import assistant_message, CompletionEvent, Message, StreamEvent, user_message
from ..providers import BaseProvider
from ..tool import Tool
from .mailbox import (
    FileMailbox,
    MailboxMessage,
    MemoryMailbox,
    create_idle_notification,
)
from .team_files import (
    TEAM_LEAD_NAME,
    ensure_team_directories,
    get_team_mailbox_dir,
    remove_team_member,
    set_member_active,
    upsert_team_member,
)
from .teammate_sidecar import launch_teammate_command_sidecar, teammate_external_only

logger = logging.getLogger(__name__)


class TeammateStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    IDLE = "idle"
    SHUTDOWN = "shutdown"
    FAILED = "failed"


@dataclass
class TeammateIdentity:
    """Identity of a teammate in a team."""
    agent_id: str
    agent_name: str
    team_name: str
    color: str | None = None
    plan_mode_required: bool = False
    parent_session_id: str = ""


@dataclass
class TeammateState:
    """Observable state of a persistent teammate."""
    identity: TeammateIdentity
    status: TeammateStatus = TeammateStatus.STARTING
    current_task: str = ""
    messages_processed: int = 0
    total_turns: int = 0
    error: str = ""
    is_idle: bool = False


@dataclass
class SharedTaskList:
    """Simple shared task list for auto-claiming.

    Tasks are dicts with at minimum ``id``, ``subject``, ``status``,
    ``owner``. The teammate claims a task by setting ``owner`` and
    ``status`` to ``in_progress``.
    """

    _tasks: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        subject: str,
        *,
        description: str = "",
        task_id: str = "",
        blocked_by: list[str] | None = None,
    ) -> str:
        tid = task_id or f"task-{uuid4().hex[:6]}"
        self._tasks.append({
            "id": tid,
            "subject": subject,
            "description": description,
            "status": "pending",
            "owner": "",
            "blocked_by": list(blocked_by or []),
        })
        return tid

    def upsert(
        self,
        *,
        task_id: str,
        subject: str,
        description: str = "",
        status: str = "pending",
        owner: str = "",
        blocked_by: list[str] | None = None,
    ) -> None:
        for task in self._tasks:
            if task["id"] == task_id:
                task["subject"] = subject
                task["description"] = description
                task["status"] = status
                task["owner"] = owner
                task["blocked_by"] = list(blocked_by or [])
                return
        self._tasks.append(
            {
                "id": task_id,
                "subject": subject,
                "description": description,
                "status": status,
                "owner": owner,
                "blocked_by": list(blocked_by or []),
            }
        )

    def remove(self, task_id: str) -> None:
        self._tasks = [task for task in self._tasks if task["id"] != task_id]

    def claim_next(self, owner: str) -> dict[str, Any] | None:
        """Claim the first available (pending, unblocked) task."""
        unresolved = {
            t["id"] for t in self._tasks if t["status"] != "completed"
        }
        for task in self._tasks:
            if task["status"] != "pending":
                continue
            if task["owner"]:
                continue
            if any(b in unresolved for b in task.get("blocked_by", [])):
                continue
            task["owner"] = owner
            task["status"] = "in_progress"
            return task
        return None

    def complete(self, task_id: str) -> None:
        for t in self._tasks:
            if t["id"] == task_id:
                t["status"] = "completed"
                return

    def fail(self, task_id: str) -> None:
        for t in self._tasks:
            if t["id"] == task_id:
                t["status"] = "failed"
                return

    def list_all(self) -> list[dict[str, Any]]:
        return list(self._tasks)

    def list_pending(self) -> list[dict[str, Any]]:
        return [t for t in self._tasks if t["status"] == "pending"]


# =====================================================================
# Teammate runner
# =====================================================================

@dataclass
class TeammateConfig:
    """Configuration for spawning a persistent teammate."""
    name: str
    team_name: str
    initial_prompt: str
    system_prompt: str = "You are a helpful assistant."
    tools: list[Tool] = field(default_factory=list)
    provider: BaseProvider | None = None
    max_turns_per_prompt: int = 15
    color: str | None = None
    model: str = ""
    working_directory: str = ""
    plan_mode_required: bool = False


@dataclass(slots=True)
class _PromptExecutionResult:
    success: bool
    reply: str = ""
    error: str = ""


class PersistentTeammate:
    """A long-lived agent that persists between tasks.

    Usage::

        teammate = PersistentTeammate(
            config=TeammateConfig(name="researcher", team_name="fix-auth", ...),
            mailbox=shared_mailbox,
            provider=provider,
        )
        # Fire and forget
        asyncio.create_task(teammate.run())

        # Send it new work later
        mailbox.send("researcher", MailboxMessage(from_agent="leader", text="New task"))
    """

    def __init__(
        self,
        *,
        config: TeammateConfig,
        mailbox: MemoryMailbox | FileMailbox,
        provider: BaseProvider,
        task_list: SharedTaskList | None = None,
        on_state_change: Any | None = None,
    ) -> None:
        self._config = config
        self._mailbox = mailbox
        self._provider = provider
        self._task_list = task_list
        self._on_state_change = on_state_change

        self._identity = TeammateIdentity(
            agent_id=f"{config.name}@{config.team_name}",
            agent_name=config.name,
            team_name=config.team_name,
            color=config.color,
            plan_mode_required=config.plan_mode_required,
        )
        self._state = TeammateState(identity=self._identity)
        self._abort = asyncio.Event()
        self._all_messages: list[Message] = []
        self._active_task_id: str = ""
        self._poll_interval = 0.5
        self._plan_approved = not self._identity.plan_mode_required

    @property
    def state(self) -> TeammateState:
        return self._state

    @property
    def identity(self) -> TeammateIdentity:
        return self._identity

    @property
    def agent_id(self) -> str:
        return self._identity.agent_id

    def shutdown(self) -> None:
        """Request the teammate to shut down."""
        self._abort.set()

    async def run(self) -> None:
        """Main loop: execute prompts, go idle, poll for more work."""
        current_prompt = self._config.initial_prompt
        self._update_state(status=TeammateStatus.STARTING)

        try:
            while not self._abort.is_set():
                self._update_state(
                    status=TeammateStatus.RUNNING,
                    current_task=current_prompt[:80],
                    is_idle=False,
                )

                execution = await self._execute_prompt(current_prompt)

                completed_task_id = self._active_task_id
                completed_status: str | None = None
                idle_reason = "available"
                failure_reason = ""

                if completed_task_id and self._task_list is not None:
                    if execution.success:
                        self._task_list.complete(completed_task_id)
                        completed_status = "resolved"
                    else:
                        self._task_list.fail(completed_task_id)
                        completed_status = "failed"
                        idle_reason = "failed"
                        failure_reason = execution.error
                    self._active_task_id = ""
                elif not execution.success:
                    idle_reason = "failed"
                    failure_reason = execution.error

                if self._abort.is_set():
                    break

                self._update_state(
                    status=TeammateStatus.IDLE,
                    current_task="",
                    error="" if execution.success else execution.error,
                    is_idle=True,
                )

                self._send_idle_notification(
                    idle_reason=idle_reason,
                    completed_task_id=completed_task_id or self.agent_id,
                    completed_status=completed_status,
                    summary=execution.reply if execution.success else execution.error,
                    failure_reason=failure_reason,
                )

                next_prompt = await self._wait_for_work()

                if next_prompt is None:
                    break

                current_prompt = next_prompt

        except Exception as exc:
            logger.error("Teammate %s failed: %s", self.agent_id, exc)
            self._update_state(
                status=TeammateStatus.FAILED,
                error=str(exc),
            )
        finally:
            self._update_state(status=TeammateStatus.SHUTDOWN)
            set_member_active(self._identity.team_name, self.agent_id, False)
            logger.info("Teammate %s shut down", self.agent_id)

    async def _execute_prompt(self, prompt: str) -> _PromptExecutionResult:
        """Run one agent turn for the given prompt."""
        from .subagent import run_subagent

        effective_prompt = prompt
        if self._identity.plan_mode_required and not self._plan_approved:
            effective_prompt = (
                "Plan approval is required before implementation.\n"
                "First analyze the task and write a concrete implementation plan.\n"
                "Then use the SendMessage tool to send the team lead a structured message exactly like this:\n"
                '{"type":"plan_approval_request","planContent":"<your plan>"}\n'
                "Do not make file changes or run write-capable commands until the team lead responds with "
                '{"type":"plan_approval_response","approve":true}.\n\n'
                f"Task:\n{prompt}"
            )

        try:
            reply = await run_subagent(
                provider=self._provider,
                system_prompt=self._config.system_prompt,
                user_text=effective_prompt,
                tools=self._config.tools,
                parent_messages=self._all_messages[-20:] if self._all_messages else None,
                max_turns=self._config.max_turns_per_prompt,
                agent_id=self.agent_id,
                runtime_overrides=(
                    {"working_directory": self._config.working_directory}
                    if self._config.working_directory
                    else None
                ),
            )

            self._all_messages.append(user_message(effective_prompt))
            self._all_messages.append(assistant_message(reply))
            self._state.messages_processed += 1
            self._state.total_turns += 1

            logger.debug(
                "Teammate %s completed prompt, reply: %s chars",
                self.agent_id, len(reply),
            )
            return _PromptExecutionResult(success=True, reply=reply)

        except Exception as exc:
            logger.warning("Teammate %s prompt failed: %s", self.agent_id, exc)
            return _PromptExecutionResult(success=False, error=str(exc))

    async def _wait_for_work(self) -> str | None:
        """Poll mailbox + task list for next work item.

        Returns the next prompt string, or None to shut down.
        """
        while not self._abort.is_set():
            msgs = self._mailbox.read_and_mark(self._identity.agent_name)

            for msg in msgs:
                try:
                    raw_payload = json.loads(msg.text)
                except Exception:
                    raw_payload = None
                if isinstance(raw_payload, dict):
                    raw_type = str(raw_payload.get("type", "")).strip()
                    if raw_type == "shutdown_request":
                        request_id = str(raw_payload.get("request_id", "")).strip() or uuid4().hex[:12]
                        response = {
                            "type": "shutdown_response",
                            "request_id": request_id,
                            "approve": True,
                            "from": self._identity.agent_name,
                            "timestamp": datetime.now().isoformat(),
                        }
                        self._mailbox.send(
                            TEAM_LEAD_NAME,
                            MailboxMessage(
                                from_agent=self._identity.agent_name,
                                text=json.dumps(response, ensure_ascii=False),
                                msg_type="shutdown",
                                color=self._identity.color,
                            ),
                        )
                        logger.info(
                            "Teammate %s approved shutdown request %s",
                            self.agent_id, request_id,
                        )
                        return None

                try:
                    team_msg = TeamMessage.decode(msg.text)
                except Exception:
                    team_msg = None
                if team_msg is not None:
                    payload = team_msg.payload
                    if isinstance(payload, dict):
                        structured_text = (
                            payload.get("task")
                            or payload.get("message")
                            or payload.get("content")
                            or ""
                        )
                        if structured_text:
                            return str(structured_text)

                if isinstance(raw_payload, dict):
                    raw_type = str(raw_payload.get("type", "")).strip()
                    if raw_type == "plan_approval_request":
                        plan_text = str(raw_payload.get("planContent", "")).strip()
                        if plan_text:
                            return (
                                "The team lead requested plan review. "
                                "Review the following proposed plan and respond accordingly:\n\n"
                                f"{plan_text}"
                            )
                        return "The team lead requested plan review."
                    if raw_type == "plan_approval_response":
                        approved = bool(raw_payload.get("approve", False))
                        if approved:
                            self._plan_approved = True
                            permission_mode = str(raw_payload.get("permissionMode", "")).strip()
                            suffix = f" Restored permission mode: {permission_mode}." if permission_mode else ""
                            return f"The team lead approved your proposed plan. Proceed with implementation.{suffix}"
                        self._plan_approved = False
                        feedback = str(raw_payload.get("feedback", "")).strip() or "The proposed plan was rejected. Revise it."
                        return f"Your proposed plan was rejected by the team lead. Feedback: {feedback}"
                    if raw_type == "shutdown_response":
                        if bool(raw_payload.get("approve", False)):
                            return None
                        reason = str(raw_payload.get("reason", "")).strip() or "Shutdown rejected."
                        return f"Shutdown request was rejected. Continue working. Reason: {reason}"

                return msg.text

            if self._task_list is not None:
                task = self._task_list.claim_next(self._identity.agent_name)
                if task is not None:
                    self._active_task_id = task["id"]
                    if self._identity.plan_mode_required:
                        self._plan_approved = False
                    prompt = f"Complete task #{task['id']}: {task['subject']}"
                    if task.get("description"):
                        prompt += f"\n\n{task['description']}"
                    logger.info(
                        "Teammate %s claimed task %s",
                        self.agent_id, task["id"],
                    )
                    return prompt

            try:
                await asyncio.wait_for(
                    self._abort.wait(),
                    timeout=self._poll_interval,
                )
                return None
            except asyncio.TimeoutError:
                pass

        return None

    def _send_idle_notification(
        self,
        *,
        idle_reason: str = "available",
        completed_task_id: str = "",
        completed_status: str | None = None,
        summary: str = "",
        failure_reason: str = "",
    ) -> None:
        """Notify the leader that this teammate is idle."""
        notif = create_idle_notification(
            self._identity.agent_name,
            idle_reason=idle_reason,
            summary=summary,
            completed_task_id=completed_task_id,
            completed_status=completed_status,
            failure_reason=failure_reason,
        )
        self._mailbox.send(
            "team-lead",
            MailboxMessage(
                from_agent=self._identity.agent_name,
                text=notif.to_text(),
                msg_type="idle",
                color=self._identity.color,
            ),
        )

    def _update_state(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            if hasattr(self._state, k):
                setattr(self._state, k, v)
        if self._on_state_change is not None:
            try:
                self._on_state_change(self._state)
            except Exception:
                pass


# =====================================================================
# Team — manages a group of persistent teammates
# =====================================================================

@dataclass
class TeamConfig:
    """Configuration for a team of persistent teammates."""
    team_name: str
    description: str = ""


@dataclass(slots=True)
class _ExternalTeammateHandle:
    """Sidecar process for MINI_AGENT_TEAMMATE_EXTERNAL_ONLY — no PersistentTeammate."""

    proc: subprocess.Popen[Any]
    agent_name: str


def _terminate_sidecar(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()


class Team:
    """Manages a group of persistent teammates with a shared mailbox.

    Usage::

        team = Team(provider=provider, config=TeamConfig(team_name="fix-auth"))

        team.spawn_teammate(TeammateConfig(
            name="researcher",
            team_name="fix-auth",
            initial_prompt="Research the auth module",
            tools=read_only_tools,
        ))

        team.spawn_teammate(TeammateConfig(
            name="implementer",
            team_name="fix-auth",
            initial_prompt="Wait for instructions",
            tools=all_tools,
        ))

        # Send message to a teammate
        team.send_message("researcher", "Now look at the tests")

        # Shut down the whole team
        await team.shutdown_all()
    """

    def __init__(
        self,
        *,
        provider: BaseProvider,
        config: TeamConfig,
        mailbox: MemoryMailbox | FileMailbox | None = None,
        task_list: SharedTaskList | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._mailbox = mailbox or MemoryMailbox()
        self._task_list = task_list or SharedTaskList()
        self._teammates: dict[str, PersistentTeammate] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._external: dict[str, _ExternalTeammateHandle] = {}
        self._discovery = TeammateDiscovery()

    @property
    def team_name(self) -> str:
        return self._config.team_name

    @property
    def mailbox(self) -> MemoryMailbox | FileMailbox:
        return self._mailbox

    @property
    def task_list(self) -> SharedTaskList:
        return self._task_list

    def _resolve_mailbox_target(self, agent_ref: str) -> str | None:
        for agent_id, teammate in self._teammates.items():
            if agent_ref == agent_id or agent_ref == teammate.identity.agent_name:
                return teammate.identity.agent_name
        for agent_id, handle in self._external.items():
            if agent_ref == agent_id or agent_ref == handle.agent_name:
                return handle.agent_name
        return None

    def _ensure_file_mailbox_for_external(self, config: TeammateConfig) -> None:
        """External workers read ``FileMailbox`` on disk; align leader mailbox."""
        mb_dir = get_team_mailbox_dir(config.team_name)
        if isinstance(self._mailbox, MemoryMailbox):
            if self._teammates:
                raise RuntimeError(
                    "MINI_AGENT_TEAMMATE_EXTERNAL_ONLY: cannot add an external teammate "
                    "while in-process teammates exist on MemoryMailbox; use FileMailbox "
                    "for the team or spawn external teammates first."
                )
            self._mailbox = FileMailbox(mb_dir)
            logger.info(
                "Team mailbox switched to FileMailbox at %s for external-only teammate",
                mb_dir,
            )
            return
        if isinstance(self._mailbox, FileMailbox):
            if Path(self._mailbox._base).resolve() != Path(mb_dir).resolve():  # noqa: SLF001
                raise ValueError(
                    "FileMailbox directory does not match this team's mailboxes; "
                    "cannot add external-only teammate."
                )

    def spawn_teammate(self, config: TeammateConfig) -> str:
        """Spawn a new persistent teammate. Returns agent_id."""
        ensure_team_directories(config.team_name)
        agent_id = f"{config.name}@{config.team_name}"

        if teammate_external_only():
            self._ensure_file_mailbox_for_external(config)
            sidecar = launch_teammate_command_sidecar(config)
            if sidecar is None:
                raise RuntimeError(
                    "MINI_AGENT_TEAMMATE_EXTERNAL_ONLY=1 requires MINI_AGENT_TEAMMATE_COMMAND "
                    "to spawn the external worker process."
                )
            self._discovery.register(
                PeerInfo(
                    peer_id=agent_id,
                    pid=sidecar.pid,
                    name=config.name,
                    capabilities=[tool.name for tool in config.tools],
                    team_name=self.team_name,
                )
            )
            self._external[agent_id] = _ExternalTeammateHandle(
                proc=sidecar,
                agent_name=config.name,
            )
            upsert_team_member(
                self.team_name,
                {
                    "agentId": agent_id,
                    "name": config.name,
                    "agentType": config.name,
                    "model": config.model,
                    "joinedAt": int(datetime.now().timestamp() * 1000),
                    "tmuxPaneId": "",
                    "cwd": config.working_directory or os.getcwd(),
                    "subscriptions": [],
                    "isActive": True,
                    "backendType": "subprocess-external-only",
                },
            )
            logger.info(
                "Spawned external-only teammate %s in team %s (sidecar pid=%s)",
                agent_id,
                self.team_name,
                sidecar.pid,
            )
            return agent_id

        sidecar = launch_teammate_command_sidecar(config)
        teammate = PersistentTeammate(
            config=config,
            mailbox=self._mailbox,
            provider=self._provider,
            task_list=self._task_list,
        )
        agent_id = teammate.agent_id
        self._teammates[agent_id] = teammate
        self._discovery.register(
            PeerInfo(
                peer_id=agent_id,
                pid=os.getpid(),
                name=teammate.identity.agent_name,
                capabilities=[tool.name for tool in config.tools],
                team_name=self.team_name,
            )
        )
        self._tasks[agent_id] = asyncio.create_task(
            teammate.run(),
            name=f"teammate-{agent_id}",
        )
        backend = (
            "in-process+subprocess-sidecar"
            if sidecar is not None
            else "in-process"
        )
        upsert_team_member(
            self.team_name,
            {
                "agentId": agent_id,
                "name": teammate.identity.agent_name,
                "agentType": config.name,
                "model": config.model,
                "joinedAt": int(datetime.now().timestamp() * 1000),
                "tmuxPaneId": "",
                "cwd": config.working_directory or os.getcwd(),
                "subscriptions": [],
                "isActive": True,
                "backendType": backend,
            },
        )
        logger.info("Spawned teammate %s in team %s", agent_id, self.team_name)
        return agent_id

    def send_message(self, agent_name: str, text: str) -> bool:
        """Send a message to a teammate by agent_id or teammate name."""
        if agent_name in {"*", "all"}:
            return self.broadcast(text) > 0
        target = self._resolve_mailbox_target(agent_name)
        if target is None:
            return False
        self._mailbox.send(
            target,
            MailboxMessage(from_agent="team-lead", text=text),
        )
        return True

    def broadcast(self, text: str) -> int:
        """Broadcast a structured message to all known teammates."""
        message = TeamMessage(
            sender="team-lead",
            receiver="*",
            msg_type=TeamMessageType.BROADCAST,
            payload={"message": text},
        )
        return broadcast_message(
            self._discovery,
            message,
            mailbox=self._mailbox,
            exclude_self=False,
        )

    def send_shutdown(self, agent_name: str, reason: str = "") -> None:
        """Send a shutdown request to a teammate by agent_id or name."""
        target = self._resolve_mailbox_target(agent_name)
        if target is None:
            return
        payload = {
            "type": "shutdown_request",
            "request_id": uuid4().hex[:12],
            "from": TEAM_LEAD_NAME,
        }
        if reason:
            payload["reason"] = reason
        self._mailbox.send(
            target,
            MailboxMessage(
                from_agent="team-lead",
                text=json.dumps(payload, ensure_ascii=False),
                msg_type="shutdown",
            ),
        )

    def get_teammate(self, agent_id: str) -> PersistentTeammate | None:
        return self._teammates.get(agent_id)

    def list_teammates(self) -> list[TeammateState]:
        out = [t.state for t in self._teammates.values()]
        for agent_id, handle in self._external.items():
            ident = TeammateIdentity(
                agent_id=agent_id,
                agent_name=handle.agent_name,
                team_name=self.team_name,
            )
            out.append(
                TeammateState(
                    identity=ident,
                    status=TeammateStatus.IDLE,
                    is_idle=True,
                )
            )
        return out

    def get_idle_teammates(self) -> list[str]:
        ids = [
            t.agent_id for t in self._teammates.values()
            if t.state.is_idle
        ]
        ids.extend(self._external.keys())
        return ids

    async def shutdown_teammate(self, agent_name: str) -> None:
        """Send shutdown and wait for teammate to exit."""
        self.send_shutdown(agent_name)
        for aid, handle in list(self._external.items()):
            if handle.agent_name == agent_name or aid == agent_name:
                _terminate_sidecar(handle.proc)
                self._discovery.unregister(aid)
                remove_team_member(self.team_name, aid)
                del self._external[aid]
                return
        for aid, t in self._teammates.items():
            if t.identity.agent_name == agent_name or aid == agent_name:
                t.shutdown()
                task = self._tasks.get(aid)
                if task and not task.done():
                    try:
                        await asyncio.wait_for(task, timeout=5.0)
                    except asyncio.TimeoutError:
                        task.cancel()
                self._discovery.unregister(aid)
                remove_team_member(self.team_name, aid)
                break

    async def shutdown_all(self) -> None:
        """Shut down all teammates gracefully."""
        for t in self._teammates.values():
            self.send_shutdown(t.identity.agent_name, "Team shutting down")
            t.shutdown()
            set_member_active(self.team_name, t.identity.agent_id, False)

        for aid, handle in list(self._external.items()):
            self.send_shutdown(handle.agent_name, "Team shutting down")
            set_member_active(self.team_name, aid, False)

        tasks = [t for t in self._tasks.values() if not t.done()]
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=10.0)
            for p in pending:
                p.cancel()

        for aid, handle in list(self._external.items()):
            _terminate_sidecar(handle.proc)
            self._discovery.unregister(aid)
            remove_team_member(self.team_name, aid)
        self._external.clear()

        self._teammates.clear()
        self._tasks.clear()
        self._discovery.unregister()
        logger.info("Team %s shut down", self.team_name)

    def has_active_teammates(self) -> bool:
        if any(
            t.state.status in (TeammateStatus.RUNNING, TeammateStatus.IDLE)
            for t in self._teammates.values()
        ):
            return True
        return any(
            handle.proc.poll() is None
            for handle in self._external.values()
        )


# =====================================================================
# Structured message protocol
# =====================================================================

class TeamMessageType(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"
    BROADCAST = "broadcast"
    HEARTBEAT = "heartbeat"
    ERROR = "error"


@dataclass
class TeamMessage:
    """Structured inter-agent message with correlation tracking.

    Usage::

        msg = TeamMessage(
            sender="researcher",
            receiver="implementer",
            msg_type=TeamMessageType.REQUEST,
            payload={"task": "fix auth bug"},
        )
        encoded = msg.encode()
        decoded = TeamMessage.decode(encoded)
    """
    sender: str
    receiver: str
    msg_type: TeamMessageType
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            from datetime import datetime as _dt
            self.timestamp = _dt.now().isoformat()
        if not self.correlation_id:
            self.correlation_id = uuid4().hex[:12]

    def encode(self) -> str:
        """Serialize to JSON string."""
        import json
        return json.dumps({
            "sender": self.sender,
            "receiver": self.receiver,
            "type": self.msg_type.value,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
        })

    @classmethod
    def decode(cls, data: str) -> TeamMessage:
        """Deserialize from JSON string."""
        import json
        d = json.loads(data)
        return cls(
            sender=d["sender"],
            receiver=d["receiver"],
            msg_type=TeamMessageType(d["type"]),
            payload=d.get("payload", {}),
            timestamp=d.get("timestamp", ""),
            correlation_id=d.get("correlation_id", ""),
        )

    def make_response(self, payload: dict[str, Any]) -> TeamMessage:
        """Create a response message to this request."""
        return TeamMessage(
            sender=self.receiver,
            receiver=self.sender,
            msg_type=TeamMessageType.RESPONSE,
            payload=payload,
            correlation_id=self.correlation_id,
        )

    def make_error(self, error: str) -> TeamMessage:
        """Create an error response to this message."""
        return TeamMessage(
            sender=self.receiver,
            receiver=self.sender,
            msg_type=TeamMessageType.ERROR,
            payload={"error": error},
            correlation_id=self.correlation_id,
        )


# =====================================================================
# Peer discovery — file-based presence
# =====================================================================

@dataclass
class PeerInfo:
    """Presence record for a discovered peer agent."""
    peer_id: str
    pid: int
    name: str
    capabilities: list[str] = field(default_factory=list)
    started_at: str = ""
    team_name: str = ""


class TeammateDiscovery:
    """File-based peer discovery for cross-process agent coordination.

    Each peer writes a ``{pid}.json`` file to ``~/.mini_agent/peers/``.
    Other agents scan this directory to find live peers.

    Usage::

        discovery = TeammateDiscovery()
        discovery.register(PeerInfo(
            peer_id="researcher-1", pid=12345, name="researcher",
            capabilities=["search", "read"],
        ))
        peers = discovery.discover_peers()
        discovery.unregister()
    """

    def __init__(self, base_dir: str | None = None) -> None:
        from pathlib import Path
        if base_dir:
            self._base = Path(base_dir)
        else:
            self._base = mini_agent_path("peers")
        try:
            self._base.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._base = Path(tempfile.gettempdir()) / "mini_agent_peers"
            self._base.mkdir(parents=True, exist_ok=True)
        self._my_pid = os.getpid()
        self._my_paths: dict[str, Path] = {}

    def _path_for(self, peer_id: str, pid: int) -> Path:
        safe = re.sub(r"[^\w\-.]", "_", peer_id)
        return self._base / f"{safe}-{pid}.json"

    def register(self, info: PeerInfo) -> None:
        """Write presence file for this peer."""
        import json
        path = self._path_for(info.peer_id, info.pid)
        data = {
            "peer_id": info.peer_id,
            "pid": info.pid,
            "name": info.name,
            "capabilities": info.capabilities,
            "started_at": info.started_at or datetime.now().isoformat(),
            "team_name": info.team_name,
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self._my_paths[info.peer_id] = path

    def unregister(self, peer_id: str | None = None) -> None:
        """Remove one registered peer or all local peer presence files."""
        if peer_id is not None:
            path = self._my_paths.pop(peer_id, None)
            if path and path.exists():
                path.unlink(missing_ok=True)
            return
        for path in self._my_paths.values():
            if path.exists():
                path.unlink(missing_ok=True)
        self._my_paths.clear()

    def discover_peers(self, *, include_self: bool = False) -> list[PeerInfo]:
        """Scan the peers directory and return live peers."""
        import json
        peers: list[PeerInfo] = []
        for path in self._base.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                pid = data.get("pid", 0)
                if not include_self and pid == self._my_pid:
                    continue
                if not self.is_peer_alive(pid):
                    path.unlink(missing_ok=True)
                    continue
                peers.append(PeerInfo(
                    peer_id=data.get("peer_id", ""),
                    pid=pid,
                    name=data.get("name", ""),
                    capabilities=data.get("capabilities", []),
                    started_at=data.get("started_at", ""),
                    team_name=data.get("team_name", ""),
                ))
            except Exception:
                continue
        return peers

    @staticmethod
    def is_peer_alive(pid: int) -> bool:
        """Check if a PID is still running."""
        import os
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def cleanup_stale(self) -> int:
        """Remove presence files for dead processes. Returns count removed."""
        removed = 0
        for path in self._base.glob("*.json"):
            try:
                import json
                data = json.loads(path.read_text(encoding="utf-8"))
                pid = data.get("pid", 0)
                if not self.is_peer_alive(pid):
                    path.unlink(missing_ok=True)
                    removed += 1
            except Exception:
                pass
        return removed


# =====================================================================
# Broadcast messaging
# =====================================================================

def broadcast_message(
    discovery: TeammateDiscovery,
    message: TeamMessage,
    *,
    mailbox: MemoryMailbox | FileMailbox | None = None,
    exclude_self: bool = True,
) -> int:
    """Send a message to all known peers. Non-blocking, fire-and-forget.

    If a mailbox is provided, messages are delivered through it.
    Otherwise a file mailbox is used for peers that advertise a team.

    Returns the number of peers the message was sent to.
    """
    peers = discovery.discover_peers(include_self=not exclude_self)
    if not peers:
        return 0

    msg_text = message.encode()
    count = 0
    for peer in peers:
        if mailbox is not None:
            mailbox.send(
                peer.name,
                MailboxMessage(
                    from_agent=message.sender,
                    text=msg_text,
                    msg_type="message",
                ),
            )
            count += 1
        else:
            if peer.team_name:
                file_mailbox = FileMailbox(get_team_mailbox_dir(peer.team_name))
                file_mailbox.send(
                    peer.name,
                    MailboxMessage(
                        from_agent=message.sender,
                        text=msg_text,
                        msg_type="message",
                    ),
                )
                count += 1
            else:
                logger.debug(
                    "Broadcast to peer %s (pid=%d) skipped: no team mailbox",
                    peer.name, peer.pid,
                )
    return count
