"""Background agent runner — execute agent tasks while foreground keeps chatting.

This is the missing bridge between TaskManager, sub-agents, and the
foreground conversation.  It enables the pattern:

    User: "Refactor this module"
    Agent: "I'll work on that in the background (task bg-a1b2). Feel free to keep chatting."
    User: "What's Python's GIL?"
    Agent: answers about GIL...
    [bg-a1b2 completes]
    Agent: "Background task bg-a1b2 finished: Module refactored successfully."

Architecture::

    ┌─────────────────────────────────────────────────┐
    │  Foreground (Agent.query / Agent.submit)          │
    │  - User types messages                           │
    │  - Agent responds normally                       │
    │  - Between turns, checks completion_queue        │
    │    for finished background results               │
    └────────────────┬────────────────────────────────┘
                     │ polls
    ┌────────────────▼────────────────────────────────┐
    │  BackgroundAgentRunner                            │
    │  - completion_queue: AsyncQueue[BackgroundResult] │
    │  - Wraps sub-agent runs as TaskManager tasks      │
    │  - On completion → push to queue + notify hooks   │
    └────────────────┬────────────────────────────────┘
                     │ submit
    ┌────────────────▼────────────────────────────────┐
    │  TaskManager                                      │
    │  - Lifecycle tracking (RUNNING/COMPLETED/FAILED)  │
    │  - Cancellation support                           │
    └─────────────────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from ..messages import Message, assistant_message, user_message
from ..paths import mini_agent_path
from ..providers import BaseProvider
from .subagent import run_subagent
from .tasks import TaskInfo, TaskManager, TaskStatus, TaskType, generate_task_id

logger = logging.getLogger(__name__)


def _addressable_task_description(description: str) -> str:
    """Stable human-readable name for SendMessage: strip a trailing resume suffix.

    Legacy resumes overwrote ``TaskInfo.description`` with ``\"… (resumed)\"``; one
    strip recovers the original addressable name for follow-ups.
    """
    d = str(description).strip()
    if d.endswith(" (resumed)"):
        return d[: -len(" (resumed)")].rstrip()
    return d


def _task_description_matches_ref(info: TaskInfo, task_ref: str) -> bool:
    """Match a user ref against task description (exact or legacy resumed label)."""
    desc = info.description
    if desc == task_ref:
        return True
    if desc == f"{task_ref} (resumed)":
        return True
    return False


@dataclass
class BackgroundResult:
    """Delivered to the foreground when a background agent task completes.

    ``to_task_notification_xml`` matches the coordinator ``<task-notification>`` shape
    (including optional ``usage``) from ``coordinatorMode.ts`` / SDK task notifications.
    """
    task_id: str
    name: str
    success: bool
    status: str = ""
    reply: str = ""
    error: str = ""
    output_file: str = ""
    duration_ms: int = 0
    total_tokens: int | None = None
    tool_uses: int | None = None
    #: When the background work was started from a tool call (e.g. main-session
    #: backgrounding in reference), correlates with that ``tool_use_id``.
    tool_use_id: str | None = None

    def to_notification(self) -> str:
        status = self.status or ("completed" if self.success else "failed")
        if status == "killed":
            return f"[Background task '{self.name}' was stopped]"
        if self.success:
            preview = self.reply[:300]
            if len(self.reply) > 300:
                preview += "..."
            return f"[Background task '{self.name}' completed]\n{preview}"
        return f"[Background task '{self.name}' failed: {self.error}]"

    def to_task_notification_xml(self) -> str:
        status = self.status or ("completed" if self.success else "failed")
        summary = self._summary_for_status(status)
        parts = [
            "<task-notification>",
            f"<task-id>{escape(self.task_id)}</task-id>",
        ]
        if self.tool_use_id:
            parts.append(f"<tool_use_id>{escape(self.tool_use_id)}</tool_use_id>")
        if self.output_file:
            parts.append(f"<output_file>{escape(self.output_file)}</output_file>")
        parts.extend([
            f"<status>{escape(status)}</status>",
            f"<summary>{escape(summary)}</summary>",
        ])
        if self.reply:
            parts.append(f"<result>{escape(self.reply)}</result>")
        if self.error and status != "killed":
            parts.append(f"<reason>{escape(self.error)}</reason>")
        if (
            self.duration_ms > 0
            or self.total_tokens is not None
            or self.tool_uses is not None
        ):
            tt = 0 if self.total_tokens is None else int(self.total_tokens)
            tu = 0 if self.tool_uses is None else int(self.tool_uses)
            dm = int(self.duration_ms)
            parts.extend([
                "<usage>",
                f"  <total_tokens>{tt}</total_tokens>",
                f"  <tool_uses>{tu}</tool_uses>",
                f"  <duration_ms>{dm}</duration_ms>",
                "</usage>",
            ])
        parts.append("</task-notification>")
        return "\n".join(parts)

    def _summary_for_status(self, status: str) -> str:
        if status == "completed":
            return f"Task \"{self.name}\" completed"
        if status == "killed":
            return f"Task \"{self.name}\" was stopped"
        return f"Task \"{self.name}\" failed: {self.error or 'unknown error'}"


ToolResolver = Callable[[str | None], tuple[list[Any], str, int]]


@dataclass
class _TaskRuntimeContext:
    """Context needed to resume or continue a background task coherently."""
    system_prompt: str
    tools: list[Any] = field(default_factory=list)
    context_messages: list[Message] = field(default_factory=list)
    initial_messages: list[Message] = field(default_factory=list)
    max_turns: int = 15
    model: str = ""
    runtime_overrides: dict[str, Any] = field(default_factory=dict)


class BackgroundAgentRunner:
    """Run agent tasks in background while the foreground keeps chatting.

    Usage::

        runner = BackgroundAgentRunner(provider, task_manager)

        # Spawn a background task
        task_id = runner.spawn(
            name="refactor",
            prompt="Refactor the utils module",
            system_prompt="You are a refactoring specialist.",
        )

        # In the foreground loop, poll for completions
        while True:
            result = runner.poll_completion()
            if result:
                print(result.to_notification())
            # ... handle user input ...
    """

    def __init__(
        self,
        provider: BaseProvider,
        task_manager: TaskManager | None = None,
        *,
        tool_resolver: ToolResolver | None = None,
    ) -> None:
        self._provider = provider
        self._task_manager = task_manager or TaskManager()
        self._tool_resolver = tool_resolver
        self._completion_queue: asyncio.Queue[BackgroundResult] = asyncio.Queue()
        self._pending_messages: dict[str, list[str]] = {}
        self._task_contexts: dict[str, _TaskRuntimeContext] = {}
        self._on_complete: Callable[[BackgroundResult], Coroutine[Any, Any, None]] | None = None

    @property
    def task_manager(self) -> TaskManager:
        return self._task_manager

    def set_on_complete(
        self,
        callback: Callable[[BackgroundResult], Coroutine[Any, Any, None]],
    ) -> None:
        """Set an async callback that fires when any background task completes."""
        self._on_complete = callback

    @property
    def available_profiles(self) -> list[str]:
        """Return names of available tool profiles (empty if no resolver)."""
        if self._tool_resolver is None:
            return []
        try:
            from functools import partial
            if hasattr(self._tool_resolver, '__self__'):
                agent = self._tool_resolver.__self__  # type: ignore[union-attr]
                return list(getattr(agent, '_tool_profiles', {}).keys())
        except Exception:
            pass
        return []

    def spawn(
        self,
        *,
        name: str,
        prompt: str,
        profile: str | None = None,
        system_prompt: str | None = None,
        tools: list[Any] | None = None,
        context_messages: list[Message] | None = None,
        initial_messages: list[Message] | None = None,
        max_turns: int | None = None,
        model: str | None = None,
        runtime_overrides: dict[str, Any] | None = None,
    ) -> str:
        """Spawn a background agent task. Returns the task ID immediately.

        Args:
            profile: Named tool profile. When set and a ``tool_resolver``
                is configured, the profile's tools/prompt/max_turns are
                used as defaults.  Explicit ``tools``, ``system_prompt``
                and ``max_turns`` override the profile values.
            runtime_overrides: Optional dict; may include ``conversation_id`` for
                ``MINI_AGENT_BG_SESSIONS_LOG`` sidecar records.
        """
        resolved_tools: list[Any] = []
        resolved_prompt = "You are a helpful assistant."
        resolved_turns = 15

        if self._tool_resolver is not None:
            resolved_tools, resolved_prompt, resolved_turns = self._tool_resolver(profile)

        if not hasattr(self, "_task_contexts"):
            self._task_contexts = {}
        if not hasattr(self, "_pending_messages"):
            self._pending_messages = {}
        if not hasattr(self, "_completion_queue"):
            self._completion_queue = asyncio.Queue()

        final_tools = tools if tools is not None else resolved_tools
        final_prompt = system_prompt if system_prompt is not None else resolved_prompt
        final_turns = max_turns if max_turns is not None else resolved_turns

        task_id = generate_task_id(TaskType.LOCAL_AGENT)

        self._task_manager.submit(
            lambda: self._run_agent_task(
                task_id=task_id,
                name=name,
                prompt=prompt,
                system_prompt=final_prompt,
                tools=final_tools,
                context_messages=context_messages,
                initial_messages=initial_messages,
                max_turns=final_turns,
                model=model or "",
                runtime_overrides=dict(runtime_overrides or {}),
            ),
            name=name,
            task_id=task_id,
        )
        info = self._task_manager.get_status(task_id)
        if info is not None:
            info.output_file = str(self._output_path_for(task_id))
        self._task_contexts[task_id] = _TaskRuntimeContext(
            system_prompt=final_prompt,
            tools=list(final_tools or []),
            context_messages=list(context_messages or []),
            initial_messages=list(initial_messages or []),
            max_turns=final_turns,
            model=model or "",
            runtime_overrides=dict(runtime_overrides or {}),
        )
        logger.info(
            "Spawned background agent task: %s (%s) profile=%s tools=%d",
            name, task_id, profile or "(default)", len(final_tools),
        )
        return task_id

    def _output_path_for(self, task_id: str) -> Path:
        return mini_agent_path("task_outputs", f"{task_id}.md")

    def _write_output_file(
        self,
        task_id: str,
        *,
        name: str,
        prompt: str,
        status: str,
        reply: str = "",
        error: str = "",
    ) -> str:
        path = self._output_path_for(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = [
            f"# Task {task_id}",
            "",
            f"- name: {name}",
            f"- status: {status}",
            "",
            "## Prompt",
            "",
            prompt,
            "",
        ]
        if reply:
            body.extend(["## Output", "", reply, ""])
        if error:
            body.extend(["## Error", "", error, ""])
        path.write_text("\n".join(body), encoding="utf-8")
        return str(path)

    def poll_completion(self) -> BackgroundResult | None:
        """Non-blocking poll for a completed background result."""
        try:
            return self._completion_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def wait_completion(self, timeout: float = 0.0) -> BackgroundResult | None:
        """Async poll for a completed background result."""
        try:
            if timeout > 0:
                return await asyncio.wait_for(
                    self._completion_queue.get(), timeout=timeout
                )
            return self._completion_queue.get_nowait()
        except (asyncio.QueueEmpty, asyncio.TimeoutError):
            return None

    def drain_completions(self) -> list[BackgroundResult]:
        """Drain all pending completions from the queue."""
        results: list[BackgroundResult] = []
        while True:
            try:
                results.append(self._completion_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return results

    def get_status(self, task_id: str) -> TaskInfo | None:
        return self._task_manager.get_status(task_id)

    def list_active(self) -> list[TaskInfo]:
        return self._task_manager.list_tasks()

    def cancel(self, task_id: str) -> bool:
        task = self._task_manager.get_status(task_id)
        cancelled = self._task_manager.cancel(task_id)
        if not cancelled or task is None:
            return cancelled
        result = BackgroundResult(
            task_id=task_id,
            name=task.description,
            success=False,
            status="killed",
            error="Task was stopped",
            output_file=str(getattr(task, "output_file", "") or ""),
        )
        self._completion_queue.put_nowait(result)
        if self._on_complete is not None:
            asyncio.create_task(self._on_complete(result))
        return True

    async def cancel_all(self) -> None:
        await self._task_manager.cancel_all()

    def resolve_task_ref(self, task_ref: str) -> str | None:
        """Resolve a task id or human-readable spawn name to the canonical task id."""
        return self._resolve_task_ref(task_ref)

    def _resolve_task_ref(self, task_ref: str) -> str | None:
        task_ref = str(task_ref).strip()
        if not task_ref:
            return None
        if self._task_manager.get_status(task_ref) is not None:
            return task_ref
        matches = [
            info.id
            for info in self._task_manager.list_tasks(include_completed=True)
            if _task_description_matches_ref(info, task_ref)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def send_message(self, task_id: str, message: str) -> bool:
        """Send a follow-up message to a running or completed background agent.

        If the agent is still running, the message is queued and delivered
        at its next opportunity. If the agent has completed or was stopped,
        it is resumed with the new message.

        Returns True if the message was accepted, False if no such task.
        """
        task_id = self._resolve_task_ref(task_id) or task_id
        task = self._task_manager.get_status(task_id)
        if task is None:
            return False

        if task.status == TaskStatus.RUNNING:
            self._pending_messages.setdefault(task_id, []).append(message)
            logger.info("Queued message for running task %s", task_id)
            return True

        if task.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.KILLED,
        ):
            self._resume_agent(task_id, _addressable_task_description(task.description), message)
            return True

        return False

    def _resume_agent(self, task_id: str, name: str, message: str) -> None:
        """Resume a completed agent with a new user message."""
        stable_name = _addressable_task_description(name)
        runtime = self._task_contexts.get(task_id) or _TaskRuntimeContext(
            system_prompt="You are a helpful assistant. Continue from your previous work.",
            tools=[],
            context_messages=[],
            initial_messages=[],
            max_turns=10,
        )
        # Keep TaskInfo.description as the stable spawn name so name-based SendMessage works after resume.
        self._task_manager.submit(
            lambda: self._run_agent_task(
                task_id=task_id,
                name=f"{stable_name} (resumed)",
                prompt=message,
                system_prompt=runtime.system_prompt,
                tools=list(runtime.tools),
                context_messages=list(runtime.context_messages),
                initial_messages=[],
                max_turns=runtime.max_turns,
                model=runtime.model,
                runtime_overrides=dict(runtime.runtime_overrides),
            ),
            name=f"{stable_name} (resumed)",
            task_id=task_id,
            description=stable_name,
        )
        logger.info("Resumed agent task %s with new message", task_id)

    def get_pending_messages(self, task_id: str) -> list[str]:
        """Drain pending messages for a task (called by sub-agent)."""
        return self._pending_messages.pop(task_id, [])

    def _has_pending_followups(self, task_id: str) -> bool:
        """True if send_message queued follow-ups not yet popped (race-safe peek)."""
        return bool(self._pending_messages.get(task_id))

    async def _drain_followups_quiescent(
        self,
        task_id: str,
        *,
        active_provider: Any,
        system_prompt: str,
        tools: list[Any] | None,
        history: list[Message],
        max_turns: int,
        runtime_overrides: dict[str, Any],
        reply_ref: list[str],
    ) -> bool:
        """Run queued follow-ups until quiescent; updates ``history`` and ``reply_ref[0]``.

        Returns True if at least one ``run_subagent`` call ran.
        """
        did_any = False
        while True:
            pending = self.get_pending_messages(task_id)
            if pending:
                for followup in pending:
                    reply = await run_subagent(
                        provider=active_provider,
                        system_prompt=system_prompt,
                        user_text=followup,
                        tools=tools,
                        parent_messages=history,
                        initial_messages=None,
                        max_turns=max_turns,
                        agent_id=task_id,
                        query_source=str(runtime_overrides.get("query_source", "") or ""),
                        runtime_overrides=runtime_overrides,
                    )
                    history.extend([
                        user_message(followup),
                        assistant_message(reply),
                    ])
                    reply_ref[0] = reply
                    did_any = True
                continue
            await asyncio.sleep(0)
            if self._has_pending_followups(task_id):
                continue
            await asyncio.sleep(0)
            if self._has_pending_followups(task_id):
                continue
            break
        return did_any

    async def _post_completion_followup_rounds(
        self,
        task_id: str,
        *,
        active_provider: Any,
        system_prompt: str,
        tools: list[Any] | None,
        history: list[Message],
        max_turns: int,
        runtime_overrides: dict[str, Any],
        reply_ref: list[str],
        name: str,
        prompt: str,
        model: str,
        _started: float,
        prior_result: BackgroundResult,
        prior_output_path: str,
    ) -> tuple[BackgroundResult, str]:
        """If follow-ups are pending, run subagent turns, write output, notify; repeat until quiescent."""
        result = prior_result
        output_path = prior_output_path
        while True:
            worked = await self._drain_followups_quiescent(
                task_id,
                active_provider=active_provider,
                system_prompt=system_prompt,
                tools=tools,
                history=history,
                max_turns=max_turns,
                runtime_overrides=runtime_overrides,
                reply_ref=reply_ref,
            )
            if worked:
                output_path = self._write_output_file(
                    task_id,
                    name=name,
                    prompt=prompt,
                    status="completed",
                    reply=reply_ref[0],
                )
                _dur_ms = int((time.monotonic() - _started) * 1000)
                result = BackgroundResult(
                    task_id=task_id,
                    name=name,
                    success=True,
                    status="completed",
                    reply=reply_ref[0],
                    output_file=output_path,
                    duration_ms=_dur_ms,
                )
                self._task_contexts[task_id] = _TaskRuntimeContext(
                    system_prompt=system_prompt,
                    tools=list(tools or []),
                    context_messages=list(history),
                    initial_messages=[],
                    max_turns=max_turns,
                    model=model,
                    runtime_overrides=dict(runtime_overrides),
                )
                await self._completion_queue.put(result)

                if self._on_complete is not None:
                    try:
                        await self._on_complete(result)
                    except Exception:
                        logger.warning("on_complete callback failed for task %s", task_id)
                continue
            await asyncio.sleep(0)
            if self._has_pending_followups(task_id):
                continue
            await asyncio.sleep(0)
            if self._has_pending_followups(task_id):
                continue
            break
        return result, output_path

    # ── Internal ────────────────────────────────────────────────────

    async def _run_agent_task(
        self,
        *,
        task_id: str,
        name: str,
        prompt: str,
        system_prompt: str,
        tools: list[Any] | None,
        context_messages: list[Message] | None,
        initial_messages: list[Message] | None,
        max_turns: int,
        model: str,
        runtime_overrides: dict[str, Any],
    ) -> str:
        """Execute a sub-agent run and deliver the result to the queue."""
        _started = time.monotonic()
        worktree = runtime_overrides.get("agent_worktree")
        try:
            active_provider = self._provider
            if model:
                from ..providers import ProviderConfig, create_provider

                parent_config = getattr(self._provider, "_config", None)
                if parent_config is not None and isinstance(parent_config, ProviderConfig):
                    active_provider = create_provider(ProviderConfig(
                        type=parent_config.type,
                        model=model,
                        api_key=parent_config.api_key,
                        base_url=parent_config.base_url,
                    ))
            history = list(context_messages or [])
            reply = await run_subagent(
                provider=active_provider,
                system_prompt=system_prompt,
                user_text=prompt,
                tools=tools,
                parent_messages=context_messages,
                initial_messages=initial_messages,
                max_turns=max_turns,
                agent_id=task_id,
                query_source=str(runtime_overrides.get("query_source", "") or ""),
                runtime_overrides=runtime_overrides,
            )
            if initial_messages:
                history.extend(initial_messages)
            elif prompt:
                history.append(user_message(prompt))
            history.append(assistant_message(reply))

            reply_ref = [reply]
            await self._drain_followups_quiescent(
                task_id,
                active_provider=active_provider,
                system_prompt=system_prompt,
                tools=tools,
                history=history,
                max_turns=max_turns,
                runtime_overrides=runtime_overrides,
                reply_ref=reply_ref,
            )

            if worktree is not None:
                from ..tools.worktree import cleanup_agent_worktree

                cleanup = await cleanup_agent_worktree(worktree)
                if cleanup.get("status") == "kept":
                    reply_ref[0] = (
                        f"{reply_ref[0]}\n\n"
                        f"[worktree kept at {cleanup['worktree_path']} on branch {cleanup['branch_name']}]"
                    )
                else:
                    runtime_overrides.pop("agent_worktree", None)
                    runtime_overrides.pop("working_directory", None)

            # Follow-ups may arrive during cleanup_agent_worktree (await).
            await self._drain_followups_quiescent(
                task_id,
                active_provider=active_provider,
                system_prompt=system_prompt,
                tools=tools,
                history=history,
                max_turns=max_turns,
                runtime_overrides=runtime_overrides,
                reply_ref=reply_ref,
            )

            self._task_contexts[task_id] = _TaskRuntimeContext(
                system_prompt=system_prompt,
                tools=list(tools or []),
                context_messages=list(history),
                initial_messages=[],
                max_turns=max_turns,
                model=model,
                runtime_overrides=dict(runtime_overrides),
            )

            result: BackgroundResult
            output_path = ""
            while True:
                output_path = self._write_output_file(
                    task_id,
                    name=name,
                    prompt=prompt,
                    status="completed",
                    reply=reply_ref[0],
                )
                _dur_ms = int((time.monotonic() - _started) * 1000)
                result = BackgroundResult(
                    task_id=task_id,
                    name=name,
                    success=True,
                    status="completed",
                    reply=reply_ref[0],
                    output_file=output_path,
                    duration_ms=_dur_ms,
                )
                await self._completion_queue.put(result)

                if self._on_complete is not None:
                    try:
                        await self._on_complete(result)
                    except Exception:
                        logger.warning("on_complete callback failed for task %s", task_id)

                worked = await self._drain_followups_quiescent(
                    task_id,
                    active_provider=active_provider,
                    system_prompt=system_prompt,
                    tools=tools,
                    history=history,
                    max_turns=max_turns,
                    runtime_overrides=runtime_overrides,
                    reply_ref=reply_ref,
                )
                if worked:
                    continue
                await asyncio.sleep(0)
                if self._has_pending_followups(task_id):
                    continue
                await asyncio.sleep(0)
                if self._has_pending_followups(task_id):
                    continue
                break

            result, output_path = await self._post_completion_followup_rounds(
                task_id,
                active_provider=active_provider,
                system_prompt=system_prompt,
                tools=tools,
                history=history,
                max_turns=max_turns,
                runtime_overrides=runtime_overrides,
                reply_ref=reply_ref,
                name=name,
                prompt=prompt,
                model=model,
                _started=_started,
                prior_result=result,
                prior_output_path=output_path,
            )

            try:
                from ..services.bg_sessions import append_bg_session_record

                cid = str((runtime_overrides or {}).get("conversation_id", "") or "")
                append_bg_session_record(
                    task_id=result.task_id,
                    name=result.name,
                    status=result.status,
                    success=result.success,
                    conversation_id=cid,
                    duration_ms=int(getattr(result, "duration_ms", 0) or 0),
                    error=str(result.error or ""),
                )
            except Exception:
                logger.debug("bg_sessions record skipped", exc_info=True)

            result, output_path = await self._post_completion_followup_rounds(
                task_id,
                active_provider=active_provider,
                system_prompt=system_prompt,
                tools=tools,
                history=history,
                max_turns=max_turns,
                runtime_overrides=runtime_overrides,
                reply_ref=reply_ref,
                name=name,
                prompt=prompt,
                model=model,
                _started=_started,
                prior_result=result,
                prior_output_path=output_path,
            )
        except Exception as exc:
            if worktree is not None:
                try:
                    from ..tools.worktree import cleanup_agent_worktree

                    cleanup = await cleanup_agent_worktree(worktree)
                    if cleanup.get("status") == "removed":
                        runtime_overrides.pop("agent_worktree", None)
                        runtime_overrides.pop("working_directory", None)
                except Exception:
                    logger.warning("Failed to clean up agent worktree for task %s", task_id, exc_info=True)
            output_path = self._write_output_file(
                task_id,
                name=name,
                prompt=prompt,
                status="failed",
                error=str(exc),
            )
            _dur_ms = int((time.monotonic() - _started) * 1000)
            result = BackgroundResult(
                task_id=task_id,
                name=name,
                success=False,
                status="failed",
                error=str(exc),
                output_file=output_path,
                duration_ms=_dur_ms,
            )

        info = self._task_manager.get_status(task_id)
        if info is not None:
            info.output_file = output_path

        if not result.success:
            try:
                from ..services.bg_sessions import append_bg_session_record

                cid = str((runtime_overrides or {}).get("conversation_id", "") or "")
                append_bg_session_record(
                    task_id=result.task_id,
                    name=result.name,
                    status=result.status,
                    success=result.success,
                    conversation_id=cid,
                    duration_ms=int(getattr(result, "duration_ms", 0) or 0),
                    error=str(result.error or ""),
                )
            except Exception:
                logger.debug("bg_sessions record skipped", exc_info=True)

        # Success path: completion notifications and bg_sessions append run inside try above.
        # Failed runs: notify here only.
        if not result.success:
            await self._completion_queue.put(result)

            if self._on_complete is not None:
                try:
                    await self._on_complete(result)
                except Exception:
                    logger.warning("on_complete callback failed for task %s", task_id)

        return result.reply if result.success else f"Error: {result.error}"
