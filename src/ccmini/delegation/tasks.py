"""Task primitives aligned with Claude Code's ``Task.ts``.

This module keeps the task model intentionally small:
- a fixed set of task types/statuses
- deterministic task ID prefixes
- a compact task manager used by background agents
- dream-task helpers layered on top of the same base state

**Do not confuse with disk task lists:** ``TaskType`` / ``TaskManager`` / ``TaskInfo``
here mirror ``src/Task.ts`` (runtime background work: local_agent, dream, …).
User-visible **todo JSON** rows live in :mod:`mini_agent.tools.task_tools` /
``TaskBoard`` / ``TaskRecord``, aligned with ``src/utils/tasks.ts`` (different
``TaskStatus``: ``pending`` / ``in_progress`` / ``completed``). The ``s``-prefixed IDs for **main-session backgrounding** are defined in
reference ``LocalMainSessionTask.ts``, not in ``Task.ts`` ``generateTaskId``.
"""

from __future__ import annotations

import asyncio
import inspect
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..paths import mini_agent_path


class TaskType(str, Enum):
    LOCAL_BASH = "local_bash"
    LOCAL_AGENT = "local_agent"
    REMOTE_AGENT = "remote_agent"
    IN_PROCESS_TEAMMATE = "in_process_teammate"
    LOCAL_WORKFLOW = "local_workflow"
    MONITOR_MCP = "monitor_mcp"
    DREAM = "dream"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


def is_terminal_task_status(status: TaskStatus | str) -> bool:
    """Return whether a task status is terminal."""
    return str(status) in {
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.KILLED.value,
    }


_TASK_ID_PREFIXES: dict[TaskType, str] = {
    TaskType.LOCAL_BASH: "b",
    TaskType.LOCAL_AGENT: "a",
    TaskType.REMOTE_AGENT: "r",
    TaskType.IN_PROCESS_TEAMMATE: "t",
    TaskType.LOCAL_WORKFLOW: "w",
    TaskType.MONITOR_MCP: "m",
    TaskType.DREAM: "d",
}

_TASK_ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _normalize_tool_use_id(tool_use_id: str | None) -> str | None:
    """Match optional ``toolUseId`` in ``createTaskStateBase`` (empty → unset)."""
    if tool_use_id is None:
        return None
    stripped = str(tool_use_id).strip()
    return stripped or None


def _get_task_output_path(task_id: str) -> str:
    return str(mini_agent_path("task_outputs", f"{task_id}.md"))


def _get_task_id_prefix(task_type: TaskType) -> str:
    return _TASK_ID_PREFIXES.get(task_type, "x")


def generate_task_id(task_type: TaskType) -> str:
    """Generate a task ID using the exact byte-to-alphabet flow from ``Task.ts``."""
    prefix = _get_task_id_prefix(task_type)
    random_bytes = secrets.token_bytes(8)
    task_id = prefix
    for i in range(8):
        task_id += _TASK_ID_ALPHABET[random_bytes[i] % len(_TASK_ID_ALPHABET)]
    return task_id


@dataclass(slots=True)
class TaskInfo:
    """Python task state mirroring ``TaskStateBase`` plus runtime details."""

    id: str
    type: TaskType
    status: TaskStatus
    description: str
    tool_use_id: str | None = None
    start_time: int = 0
    end_time: int | None = None
    total_paused_ms: int | None = None
    output_file: str = ""
    output_offset: int = 0
    notified: bool = False
    result: str = ""
    error: str = ""


def create_task_state_base(
    task_id: str,
    task_type: TaskType,
    description: str,
    tool_use_id: str | None = None,
) -> TaskInfo:
    return TaskInfo(
        id=task_id,
        type=task_type,
        status=TaskStatus.PENDING,
        description=description,
        tool_use_id=_normalize_tool_use_id(tool_use_id),
        start_time=int(time.time() * 1000),
        output_file=_get_task_output_path(task_id),
        output_offset=0,
        notified=False,
    )


class TaskManager:
    """Manage background async tasks with Claude Code-style task state."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskInfo] = {}
        self._async_tasks: dict[str, asyncio.Task[Any]] = {}

    def submit(
        self,
        coro: Any,
        *,
        name: str = "",
        task_id: str | None = None,
        task_type: TaskType = TaskType.LOCAL_AGENT,
        description: str = "",
        tool_use_id: str | None = None,
    ) -> str:
        task_id = task_id or generate_task_id(task_type)
        info = create_task_state_base(
            task_id,
            task_type,
            description=description or name or task_type.value,
            tool_use_id=tool_use_id,
        )
        info.status = TaskStatus.RUNNING
        self._tasks[task_id] = info

        task = asyncio.create_task(self._run_wrapper(task_id, coro))
        self._async_tasks[task_id] = task

        def _cleanup(done_task: asyncio.Task[Any]) -> None:
            current = self._async_tasks.get(task_id)
            if current is done_task:
                self._async_tasks.pop(task_id, None)

        task.add_done_callback(_cleanup)
        return task_id

    @staticmethod
    def _materialize_awaitable(coro: Any) -> Any:
        if inspect.isawaitable(coro):
            return coro
        if callable(coro):
            produced = coro()
            if inspect.isawaitable(produced):
                return produced
            raise TypeError("Task factory must return an awaitable")
        raise TypeError("TaskManager.submit expected an awaitable or factory")

    async def _run_wrapper(self, task_id: str, coro: Any) -> None:
        info = self._tasks.get(task_id)
        if info is None:
            return
        try:
            result = await self._materialize_awaitable(coro)
            info.status = TaskStatus.COMPLETED
            info.result = "" if result is None else str(result)
        except asyncio.CancelledError:
            info.status = TaskStatus.KILLED
            raise
        except Exception as exc:
            info.status = TaskStatus.FAILED
            info.error = str(exc)
        finally:
            info.end_time = int(time.time() * 1000)

    def cancel(self, task_id: str) -> bool:
        task = self._async_tasks.get(task_id)
        if task is None or task.done():
            return False
        info = self._tasks.get(task_id)
        if info is not None:
            info.status = TaskStatus.KILLED
            info.end_time = int(time.time() * 1000)
        task.cancel()
        return True

    def get_status(self, task_id: str) -> TaskInfo | None:
        return self._tasks.get(task_id)

    def list_tasks(self, *, include_completed: bool = False) -> list[TaskInfo]:
        tasks = list(self._tasks.values())
        if include_completed:
            return tasks
        return [task for task in tasks if not is_terminal_task_status(task.status)]

    def summarise(self) -> str:
        active = self.list_tasks()
        if not active:
            return "No active background tasks."
        lines = [f"Active tasks ({len(active)}):"]
        for task in active:
            lines.append(f"  - [{task.status.value}] {task.description} (id={task.id})")
        return "\n".join(lines)

    async def cancel_all(self) -> None:
        for task_id in list(self._async_tasks):
            self.cancel(task_id)
        if self._async_tasks:
            await asyncio.gather(*self._async_tasks.values(), return_exceptions=True)

    def cleanup_completed(self, *, keep_last: int = 20) -> None:
        terminal = [task for task in self._tasks.values() if is_terminal_task_status(task.status)]
        terminal.sort(key=lambda task: task.end_time, reverse=True)
        for task in terminal[keep_last:]:
            self._tasks.pop(task.id, None)


class DreamPhase(str, Enum):
    STARTING = "starting"
    UPDATING = "updating"


@dataclass(slots=True)
class DreamTurn:
    text: str
    tool_use_count: int = 0


MAX_DREAM_TURNS = 30


@dataclass(slots=True)
class DreamTaskState:
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    phase: DreamPhase = DreamPhase.STARTING
    sessions_reviewing: int = 0
    files_touched: list[str] = field(default_factory=list)
    turns: list[DreamTurn] = field(default_factory=list)
    start_time: int = 0
    end_time: int = 0
    error: str = ""


def register_dream_task(
    task_manager: TaskManager,
    *,
    sessions_reviewing: int = 0,
    coro: Any,
) -> tuple[str, DreamTaskState]:
    task_id = generate_task_id(TaskType.DREAM)
    state = DreamTaskState(
        task_id=task_id,
        status=TaskStatus.RUNNING,
        phase=DreamPhase.STARTING,
        sessions_reviewing=sessions_reviewing,
        start_time=int(time.time() * 1000),
    )

    async def _wrapped() -> Any:
        try:
            result = await coro
            complete_dream_task(state)
            return result
        except asyncio.CancelledError:
            state.status = TaskStatus.KILLED
            state.end_time = int(time.time() * 1000)
            raise
        except Exception as exc:
            fail_dream_task(state, str(exc))
            raise

    task_manager.submit(
        _wrapped,
        name="dreaming",
        task_id=task_id,
        task_type=TaskType.DREAM,
        description="dream",
    )
    return task_id, state


def add_dream_turn(
    state: DreamTaskState,
    turn: DreamTurn,
    touched_paths: list[str] | None = None,
) -> None:
    if touched_paths:
        seen = set(state.files_touched)
        for path in touched_paths:
            if path not in seen:
                state.files_touched.append(path)
                seen.add(path)
        state.phase = DreamPhase.UPDATING
    if turn.text or turn.tool_use_count:
        state.turns = state.turns[-(MAX_DREAM_TURNS - 1):] + [turn]


def complete_dream_task(state: DreamTaskState) -> None:
    state.status = TaskStatus.COMPLETED
    state.end_time = int(time.time() * 1000)


def fail_dream_task(state: DreamTaskState, error: str = "") -> None:
    state.status = TaskStatus.FAILED
    state.error = error
    state.end_time = int(time.time() * 1000)


__all__ = [
    "DreamPhase",
    "DreamTaskState",
    "DreamTurn",
    "MAX_DREAM_TURNS",
    "TaskInfo",
    "TaskManager",
    "TaskStatus",
    "TaskType",
    "add_dream_turn",
    "complete_dream_task",
    "create_task_state_base",
    "fail_dream_task",
    "generate_task_id",
    "is_terminal_task_status",
    "register_dream_task",
]
