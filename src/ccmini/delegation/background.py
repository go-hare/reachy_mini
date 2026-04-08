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
import copy
import contextlib
import json
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from ..engine.query import QueryParams, query
from ..messages import CompletionEvent, ErrorEvent, Message, assistant_message, user_message
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
    transcript_file: str = ""
    duration_ms: int = 0
    total_tokens: int | None = None
    tool_uses: int | None = None
    worker_name: str = ""
    team_name: str = ""
    task_type: str = ""
    isolation: str = ""
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
        if self.transcript_file:
            parts.append(f"<transcript_file>{escape(self.transcript_file)}</transcript_file>")
        parts.extend([
            f"<status>{escape(status)}</status>",
            f"<summary>{escape(summary)}</summary>",
        ])
        if self.worker_name:
            parts.append(f"<worker_name>{escape(self.worker_name)}</worker_name>")
        if self.team_name:
            parts.append(f"<team_name>{escape(self.team_name)}</team_name>")
        if self.task_type:
            parts.append(f"<task_type>{escape(self.task_type)}</task_type>")
        if self.isolation:
            parts.append(f"<isolation>{escape(self.isolation)}</isolation>")
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
    task_type: TaskType = TaskType.LOCAL_AGENT
    profile: str = ""
    transcript_file: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    remote_agent: Any | None = None
    remote_conversation_id: str = ""


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
        self._on_state_change: Callable[[dict[str, Any]], Coroutine[Any, Any, None] | None] | None = None

    @property
    def task_manager(self) -> TaskManager:
        return self._task_manager

    def set_on_complete(
        self,
        callback: Callable[[BackgroundResult], Coroutine[Any, Any, None]],
    ) -> None:
        """Set an async callback that fires when any background task completes."""
        self._on_complete = callback

    def set_on_state_change(
        self,
        callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None] | None],
    ) -> None:
        """Set a callback invoked whenever a tracked task snapshot changes."""
        self._on_state_change = callback

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
        task_type: TaskType = TaskType.LOCAL_AGENT,
        system_prompt: str | None = None,
        tools: list[Any] | None = None,
        context_messages: list[Message] | None = None,
        initial_messages: list[Message] | None = None,
        max_turns: int | None = None,
        model: str | None = None,
        runtime_overrides: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
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

        task_id = generate_task_id(task_type)
        transcript_file = str(self._transcript_path_for(task_id))
        task_metadata = self._merge_task_metadata(
            metadata,
            {
                "promptPreview": prompt[:400],
                "profile": profile or "",
                "model": model or "",
                "workerName": name,
                "canResume": True,
                "resumeCount": int((metadata or {}).get("resumeCount", 0) or 0),
            },
        )

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
            task_type=task_type,
            transcript_file=transcript_file,
            metadata=task_metadata,
        )
        info = self._task_manager.get_status(task_id)
        if info is not None:
            info.output_file = str(self._output_path_for(task_id))
            info.transcript_file = transcript_file
        self._task_contexts[task_id] = _TaskRuntimeContext(
            system_prompt=final_prompt,
            tools=list(final_tools or []),
            context_messages=list(context_messages or []),
            initial_messages=list(initial_messages or []),
            max_turns=final_turns,
            model=model or "",
            runtime_overrides=dict(runtime_overrides or {}),
            task_type=task_type,
            profile=profile or "",
            transcript_file=transcript_file,
            metadata=task_metadata,
        )
        self._append_transcript_entry(
            task_id,
            role="user",
            content=prompt,
            metadata={
                "event": "spawn",
                "name": name,
                "task_type": task_type.value,
                "profile": profile or "",
            },
        )
        self._update_task_snapshot(
            task_id,
            metadata_updates=task_metadata,
            output_file=str(self._output_path_for(task_id)),
            transcript_file=transcript_file,
        )
        logger.info(
            "Spawned background agent task: %s (%s) profile=%s tools=%d",
            name, task_id, profile or "(default)", len(final_tools),
        )
        return task_id

    def _output_path_for(self, task_id: str) -> Path:
        return mini_agent_path("task_outputs", f"{task_id}.md")

    def _transcript_path_for(self, task_id: str) -> Path:
        return mini_agent_path("task_transcripts", f"{task_id}.jsonl")

    def _append_transcript_entry(
        self,
        task_id: str,
        *,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        path = self._transcript_path_for(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "task_id": task_id,
            "role": role,
            "content": content,
            "metadata": dict(metadata or {}),
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return str(path)

    @staticmethod
    def _merge_task_metadata(
        base: dict[str, Any] | None,
        updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged = dict(base or {})
        if updates:
            merged.update(updates)
        return merged

    def _update_task_snapshot(
        self,
        task_id: str,
        *,
        metadata_updates: dict[str, Any] | None = None,
        output_file: str | None = None,
        transcript_file: str | None = None,
    ) -> TaskInfo | None:
        info = self._task_manager.get_status(task_id)
        if info is None:
            return None
        if metadata_updates:
            info.metadata.update(metadata_updates)
        if output_file is not None:
            info.output_file = output_file
        if transcript_file is not None:
            info.transcript_file = transcript_file
        info.updated_at = int(time.time() * 1000)
        if self._on_state_change is not None:
            snapshot = self._task_snapshot(info)
            maybe = self._on_state_change(snapshot)
            if asyncio.iscoroutine(maybe):
                asyncio.create_task(maybe)
        return info

    async def _execute_task_turn(
        self,
        *,
        task_id: str,
        prompt: str,
        system_prompt: str,
        tools: list[Any] | None,
        parent_messages: list[Message] | None,
        initial_messages: list[Message] | None,
        max_turns: int,
        model: str,
        runtime_overrides: dict[str, Any],
        runtime_context: _TaskRuntimeContext,
        active_provider: Any,
    ) -> str:
        if runtime_context.task_type == TaskType.REMOTE_AGENT or runtime_context.metadata.get("isolation") == "remote":
            remote_agent = await self._ensure_remote_agent(
                task_id=task_id,
                runtime=runtime_context,
                system_prompt=system_prompt,
                tools=tools,
                max_turns=max_turns,
                model=model,
            )
        if not getattr(remote_agent, "_messages", None):
            seeded_messages = list(parent_messages or [])
            if initial_messages:
                seeded_messages.extend(initial_messages)
            if seeded_messages:
                remote_agent._messages = copy.deepcopy(seeded_messages)
                runtime_context.context_messages = copy.deepcopy(seeded_messages)
                with contextlib.suppress(Exception):
                    remote_agent._persist_session_snapshot()
            reply = ""
            error = ""
            if initial_messages:
                params = QueryParams(
                    provider=remote_agent._provider,
                    system_prompt=remote_agent._system_prompt,
                    messages=remote_agent._messages,
                    tools=list(remote_agent._tools),
                    agent=remote_agent,
                    conversation_id=runtime_context.remote_conversation_id or task_id,
                    agent_id=remote_agent._agent_id,
                    max_turns=max_turns,
                    compact_threshold=remote_agent._config.compact_threshold,
                    compact_config=getattr(remote_agent, "_compact_config", None),
                    compact_tracker=getattr(remote_agent, "_compact_tracker", None),
                    pre_hooks=remote_agent._hook_runner.pre_query,
                    post_hooks=remote_agent._hook_runner.post_query,
                    stream_hooks=remote_agent._hook_runner.stream_event,
                    permission_checker=remote_agent._permission_checker,
                    attachment_collector=remote_agent._attachment_collector,
                    hook_runner=remote_agent._hook_runner,
                    fallback_config=remote_agent._fallback_config,
                    summary_provider=remote_agent._summary_provider,
                    on_tool_summary=remote_agent._fire_event,
                    session_memory_content=remote_agent._get_session_memory_content(),
                    snip_config=getattr(remote_agent, "_snip_config", None),
                    collapse_config=getattr(remote_agent, "_collapse_config", None),
                    working_directory=remote_agent.working_directory,
                    query_source="embedded_remote_continue",
                    is_non_interactive=bool(getattr(remote_agent, "_runtime_is_non_interactive", False)),
                )
                async for event in query(params):
                    if isinstance(event, CompletionEvent):
                        reply = str(event.text or "")
                    elif isinstance(event, ErrorEvent):
                        error = str(event.error or "")
                remote_agent._messages = params.messages
            else:
                async for event in remote_agent.query(
                    prompt,
                    conversation_id=runtime_context.remote_conversation_id or task_id,
                    metadata={
                        "source": "embedded-remote-agent",
                        "remote_task_id": task_id,
                    },
                ):
                    if isinstance(event, CompletionEvent):
                        reply = str(event.text or "")
                    elif isinstance(event, ErrorEvent):
                        error = str(event.error or "")
            runtime_context.context_messages = copy.deepcopy(list(remote_agent.messages))
            if error:
                raise RuntimeError(error)
            return reply

        return await run_subagent(
            provider=active_provider,
            system_prompt=system_prompt,
            user_text=prompt,
            tools=tools,
            parent_messages=parent_messages,
            initial_messages=initial_messages,
            max_turns=max_turns,
            agent_id=task_id,
            conversation_id=task_id,
            query_source=str(runtime_overrides.get("query_source", "") or ""),
            runtime_overrides=runtime_overrides,
        )

    def _task_snapshot(self, info: TaskInfo) -> dict[str, Any]:
        metadata = dict(getattr(info, "metadata", {}) or {})
        return {
            "id": info.id,
            "type": info.type.value if isinstance(info.type, TaskType) else str(info.type),
            "status": info.status.value if isinstance(info.status, TaskStatus) else str(info.status),
            "description": info.description,
            "outputFile": str(info.output_file or ""),
            "transcriptFile": str(getattr(info, "transcript_file", "") or ""),
            "startTime": int(info.start_time or 0),
            "endTime": int(info.end_time or 0) if info.end_time is not None else None,
            "updatedAt": int(getattr(info, "updated_at", info.start_time or 0) or 0),
            "toolUseId": str(info.tool_use_id or ""),
            "result": str(info.result or ""),
            "error": str(info.error or ""),
            "resumeCount": int(metadata.get("resumeCount", 0) or 0),
            "canResume": bool(metadata.get("canResume", True)),
            "promptPreview": str(metadata.get("promptPreview", "") or ""),
            "model": str(metadata.get("model", "") or ""),
            "profile": str(metadata.get("profile", "") or ""),
            "workerName": str(metadata.get("workerName", "") or ""),
            "teamName": str(metadata.get("teamName", "") or ""),
            "backendType": str(metadata.get("backendType", "") or ""),
            "isolation": str(metadata.get("isolation", "") or ""),
            "agentType": str(metadata.get("agentType", "") or ""),
            "subagentType": str(metadata.get("subagentType", "") or ""),
            "metadata": metadata,
        }

    async def _ensure_remote_agent(
        self,
        *,
        task_id: str,
        runtime: _TaskRuntimeContext,
        system_prompt: str,
        tools: list[Any] | None,
        max_turns: int,
        model: str,
    ) -> Any:
        if runtime.remote_agent is not None:
            return runtime.remote_agent

        from copy import deepcopy

        from ..agent import AgentConfig
        from ..factory import create_agent

        spec = dict((runtime.runtime_overrides or {}).get("remote_executor_spec", {}) or {})
        config_obj = spec.get("config")
        if isinstance(config_obj, AgentConfig):
            config_copy = deepcopy(config_obj)
        else:
            config_copy = AgentConfig()
        config_copy.max_turns = max_turns

        conversation_id = str(spec.get("conversation_id", "") or f"remote-{task_id}").strip()
        provider = spec.get("provider", self._provider)
        if model:
            from ..providers import ProviderConfig, create_provider

            provider_config = getattr(provider, "_config", None)
            if isinstance(provider_config, ProviderConfig):
                provider = create_provider(
                    ProviderConfig(
                        type=provider_config.type,
                        model=model,
                        api_key=provider_config.api_key,
                        base_url=provider_config.base_url,
                    )
                )
        effective_tools = list(spec.get("tools", tools or []))
        remote_agent = create_agent(
            provider=provider,
            system_prompt=spec.get("system_prompt", system_prompt),
            tools=effective_tools,
            sub_agent_tools=list(spec.get("sub_agent_tools", []) or []),
            tool_profiles=dict(spec.get("tool_profiles", {}) or {}),
            hooks=list(spec.get("hooks", []) or []),
            config=config_copy,
            conversation_id=conversation_id,
            agent_id=f"remote-task-{task_id}",
            attachment_collector=spec.get("attachment_collector"),
            fallback_config=spec.get("fallback_config"),
            token_budget=spec.get("token_budget"),
            summary_provider=spec.get("summary_provider"),
            skill_prefetch=spec.get("skill_prefetch"),
            use_default_tools=False,
        )
        await remote_agent.start()
        runtime.remote_agent = remote_agent
        runtime.remote_conversation_id = conversation_id
        runtime.metadata = self._merge_task_metadata(
            runtime.metadata,
            {
                "backendType": runtime.metadata.get("backendType", "embedded-remote"),
                "isolation": runtime.metadata.get("isolation", "remote"),
                "remoteConversationId": conversation_id,
            },
        )
        self._update_task_snapshot(
            task_id,
            metadata_updates=runtime.metadata,
        )
        return remote_agent

    async def _shutdown_remote_agent(self, runtime: _TaskRuntimeContext | None) -> None:
        if runtime is None or runtime.remote_agent is None:
            return
        try:
            with contextlib.suppress(Exception):
                runtime.remote_agent.cancel_submit()
            await runtime.remote_agent.stop()
        finally:
            runtime.remote_agent = None

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
        info = self._task_manager.get_status(task_id)
        metadata = dict(getattr(info, "metadata", {}) or {})
        transcript_file = str(getattr(info, "transcript_file", "") or self._transcript_path_for(task_id))
        body = [
            f"# Task {task_id}",
            "",
            f"- name: {name}",
            f"- status: {status}",
            f"- transcript: {transcript_file}",
            f"- resumes: {int(metadata.get('resumeCount', 0) or 0)}",
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

    def discard_completion(self, task_id: str) -> bool:
        """Remove one queued completion for *task_id* while preserving order."""
        kept: list[BackgroundResult] = []
        removed = False
        while True:
            try:
                item = self._completion_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not removed and item.task_id == task_id:
                removed = True
                continue
            kept.append(item)
        for item in kept:
            self._completion_queue.put_nowait(item)
        return removed

    def get_status(self, task_id: str) -> TaskInfo | None:
        return self._task_manager.get_status(task_id)

    def list_active(self) -> list[TaskInfo]:
        return self._task_manager.list_tasks()

    def list_task_snapshots(self, *, include_completed: bool = True) -> list[dict[str, Any]]:
        tasks = self._task_manager.list_tasks(include_completed=include_completed)
        return [self._task_snapshot(task) for task in tasks]

    def cancel(self, task_id: str) -> bool:
        task = self._task_manager.get_status(task_id)
        cancelled = self._task_manager.cancel(task_id)
        if not cancelled or task is None:
            return cancelled
        updated_task = self._update_task_snapshot(
            task_id,
            metadata_updates={"canResume": False},
        )
        current_task = updated_task or task
        runtime = self._task_contexts.get(task_id)
        if runtime is not None:
            runtime.metadata = self._merge_task_metadata(
                runtime.metadata,
                {"canResume": False},
            )
            asyncio.create_task(self._shutdown_remote_agent(runtime))
        result = BackgroundResult(
            task_id=task_id,
            name=current_task.description,
            success=False,
            status="killed",
            error="Task was stopped",
            output_file=str(getattr(current_task, "output_file", "") or ""),
            transcript_file=str(getattr(current_task, "transcript_file", "") or ""),
            worker_name=str((getattr(current_task, "metadata", {}) or {}).get("workerName", "") or current_task.description),
            team_name=str((getattr(current_task, "metadata", {}) or {}).get("teamName", "") or ""),
            task_type=str(getattr(current_task.type, "value", current_task.type)),
            isolation=str((getattr(current_task, "metadata", {}) or {}).get("isolation", "") or ""),
        )
        self._completion_queue.put_nowait(result)
        if self._on_complete is not None:
            asyncio.create_task(self._on_complete(result))
        return True

    async def cancel_all(self) -> None:
        await self._task_manager.cancel_all()
        for runtime in list(self._task_contexts.values()):
            await self._shutdown_remote_agent(runtime)

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
            next_resume_count = int((task.metadata or {}).get("resumeCount", 0) or 0) + 1
            self._append_transcript_entry(
                task_id,
                role="user",
                content=message,
                metadata={"event": "queued_followup", "resumeCount": next_resume_count},
            )
            self._update_task_snapshot(
                task_id,
                metadata_updates={
                    "resumeCount": next_resume_count,
                    "canResume": True,
                },
            )
            runtime = self._task_contexts.get(task_id)
            if runtime is not None:
                runtime.metadata = self._merge_task_metadata(
                    runtime.metadata,
                    {
                        "resumeCount": next_resume_count,
                        "canResume": True,
                    },
                )
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
        existing = self._task_manager.get_status(task_id)
        current_resume_count = 0
        if existing is not None:
            current_resume_count = int((existing.metadata or {}).get("resumeCount", 0) or 0)
        next_resume_count = current_resume_count + 1
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
            task_type=runtime.task_type,
            transcript_file=runtime.transcript_file,
            metadata=self._merge_task_metadata(
                runtime.metadata,
                {
                    "canResume": True,
                    "resumeCount": next_resume_count,
                    "workerName": stable_name,
                },
            ),
        )
        self._append_transcript_entry(
            task_id,
            role="user",
            content=message,
            metadata={"event": "resume", "resumeCount": next_resume_count},
        )
        self._update_task_snapshot(
            task_id,
            metadata_updates={
                "resumeCount": next_resume_count,
                "workerName": stable_name,
                "canResume": True,
            },
            transcript_file=runtime.transcript_file or str(self._transcript_path_for(task_id)),
        )
        runtime.metadata = self._merge_task_metadata(
            runtime.metadata,
            {
                "resumeCount": next_resume_count,
                "workerName": stable_name,
                "canResume": True,
            },
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
        runtime_context: _TaskRuntimeContext,
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
                    reply = await self._execute_task_turn(
                        task_id=task_id,
                        prompt=followup,
                        system_prompt=system_prompt,
                        tools=tools,
                        parent_messages=history,
                        initial_messages=None,
                        max_turns=max_turns,
                        model=runtime_context.model,
                        runtime_overrides=runtime_overrides,
                        runtime_context=runtime_context,
                        active_provider=active_provider,
                    )
                    if runtime_context.task_type != TaskType.REMOTE_AGENT:
                        history.extend([
                            user_message(followup),
                            assistant_message(reply),
                        ])
                    else:
                        history[:] = copy.deepcopy(list(runtime_context.context_messages))
                    self._append_transcript_entry(
                        task_id,
                        role="user",
                        content=followup,
                        metadata={"event": "followup"},
                    )
                    self._append_transcript_entry(
                        task_id,
                        role="assistant",
                        content=reply,
                        metadata={"event": "followup_reply"},
                    )
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
                runtime_context=self._task_contexts.get(task_id) or _TaskRuntimeContext(
                    system_prompt=system_prompt,
                    tools=list(tools or []),
                    max_turns=max_turns,
                    model=model,
                    runtime_overrides=dict(runtime_overrides),
                ),
                reply_ref=reply_ref,
            )
            if worked:
                runtime_ctx = self._task_contexts.get(task_id)
                task_info = self._task_manager.get_status(task_id)
                metadata = dict(getattr(task_info, "metadata", {}) or getattr(runtime_ctx, "metadata", {}) or {})
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
                    transcript_file=str(getattr(task_info, "transcript_file", "") or getattr(runtime_ctx, "transcript_file", "")),
                    duration_ms=_dur_ms,
                    worker_name=str(metadata.get("workerName", "") or name),
                    team_name=str(metadata.get("teamName", "") or ""),
                    task_type=str(
                        getattr(getattr(task_info, "type", None), "value", getattr(runtime_ctx, "task_type", ""))
                    ),
                    isolation=str(metadata.get("isolation", "") or ""),
                )
                self._task_contexts[task_id] = _TaskRuntimeContext(
                    system_prompt=system_prompt,
                    tools=list(tools or []),
                    context_messages=list(history),
                    initial_messages=[],
                    max_turns=max_turns,
                    model=model,
                    runtime_overrides=dict(runtime_overrides),
                    task_type=getattr(runtime_ctx, "task_type", TaskType.LOCAL_AGENT),
                    profile=str(getattr(runtime_ctx, "profile", "") or ""),
                    transcript_file=str(getattr(task_info, "transcript_file", "") or getattr(runtime_ctx, "transcript_file", "")),
                    metadata=metadata,
                    remote_agent=getattr(runtime_ctx, "remote_agent", None),
                    remote_conversation_id=str(getattr(runtime_ctx, "remote_conversation_id", "") or ""),
                )
                self._update_task_snapshot(
                    task_id,
                    metadata_updates=metadata,
                    output_file=output_path,
                    transcript_file=result.transcript_file,
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
        task_info = self._task_manager.get_status(task_id)
        task_metadata = dict(getattr(task_info, "metadata", {}) or {})
        transcript_file = str(
            getattr(task_info, "transcript_file", "") or self._transcript_path_for(task_id)
        )
        task_type_value = str(getattr(getattr(task_info, "type", None), "value", "") or "")
        runtime_context = self._task_contexts.get(task_id) or _TaskRuntimeContext(
            system_prompt=system_prompt,
            tools=list(tools or []),
            context_messages=list(context_messages or []),
            initial_messages=list(initial_messages or []),
            max_turns=max_turns,
            model=model,
            runtime_overrides=dict(runtime_overrides),
            task_type=getattr(task_info, "type", TaskType.LOCAL_AGENT) if task_info is not None else TaskType.LOCAL_AGENT,
            profile=str(task_metadata.get("profile", "") or ""),
            transcript_file=transcript_file,
            metadata=task_metadata,
        )
        self._update_task_snapshot(
            task_id,
            metadata_updates=task_metadata,
            transcript_file=transcript_file,
        )
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
            reply = await self._execute_task_turn(
                task_id=task_id,
                prompt=prompt,
                system_prompt=system_prompt,
                tools=tools,
                parent_messages=context_messages,
                initial_messages=initial_messages,
                max_turns=max_turns,
                model=model,
                runtime_overrides=runtime_overrides,
                runtime_context=runtime_context,
                active_provider=active_provider,
            )
            if initial_messages:
                history.extend(initial_messages)
            elif prompt:
                history.append(user_message(prompt))
            if runtime_context.task_type != TaskType.REMOTE_AGENT:
                history.append(assistant_message(reply))
            else:
                history = copy.deepcopy(list(runtime_context.context_messages))
            self._append_transcript_entry(
                task_id,
                role="assistant",
                content=reply,
                metadata={"event": "completion"},
            )

            reply_ref = [reply]
            await self._drain_followups_quiescent(
                task_id,
                active_provider=active_provider,
                system_prompt=system_prompt,
                tools=tools,
                history=history,
                max_turns=max_turns,
                runtime_overrides=runtime_overrides,
                runtime_context=runtime_context,
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
                runtime_context=runtime_context,
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
                task_type=getattr(task_info, "type", TaskType.LOCAL_AGENT) if task_info is not None else TaskType.LOCAL_AGENT,
                profile=str(task_metadata.get("profile", "") or ""),
                transcript_file=transcript_file,
                metadata=task_metadata,
                remote_agent=runtime_context.remote_agent,
                remote_conversation_id=runtime_context.remote_conversation_id,
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
                    transcript_file=transcript_file,
                    duration_ms=_dur_ms,
                    worker_name=str(task_metadata.get("workerName", "") or name),
                    team_name=str(task_metadata.get("teamName", "") or ""),
                    task_type=task_type_value,
                    isolation=str(task_metadata.get("isolation", "") or ""),
                )
                self._update_task_snapshot(
                    task_id,
                    metadata_updates=task_metadata,
                    output_file=output_path,
                    transcript_file=transcript_file,
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
                    runtime_context=self._task_contexts.get(task_id) or _TaskRuntimeContext(
                        system_prompt=system_prompt,
                        tools=list(tools or []),
                        max_turns=max_turns,
                        model=model,
                        runtime_overrides=dict(runtime_overrides),
                    ),
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
            self._append_transcript_entry(
                task_id,
                role="system",
                content=str(exc),
                metadata={"event": "error"},
            )
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
                transcript_file=transcript_file,
                duration_ms=_dur_ms,
                worker_name=str(task_metadata.get("workerName", "") or name),
                team_name=str(task_metadata.get("teamName", "") or ""),
                task_type=task_type_value,
                isolation=str(task_metadata.get("isolation", "") or ""),
            )

        info = self._task_manager.get_status(task_id)
        if info is not None:
            info.output_file = output_path
            info.transcript_file = transcript_file
            info.metadata.update(task_metadata)
            info.updated_at = int(time.time() * 1000)

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

        if runtime_context.task_type == TaskType.REMOTE_AGENT:
            await self._shutdown_remote_agent(
                self._task_contexts.get(task_id) or runtime_context
            )

        return result.reply if result.success else f"Error: {result.error}"
