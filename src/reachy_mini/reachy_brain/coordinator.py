"""Coordinator for v4 long-running Brain workers."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Awaitable, Callable

from reachy_mini.action_runtime import ActionResult, ActionSpec, CancelToken

from .memory import WorkerMemory
from .worker import (
    FakePatrolWorker,
    TaskSpec,
    Worker,
    WorkerContext,
    WorkerEvent,
    WorkerResult,
    memory_root_for,
    worker_result_to_xml,
)


class WorkerCoordinator:
    """Spawn, cancel, and observe worker tasks without blocking the main agent."""

    def __init__(
        self,
        *,
        emit_action: Callable[[ActionSpec], Awaitable[ActionResult]],
        memory_root: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a worker coordinator."""
        self.emit_action = emit_action
        self.memory_root = memory_root_for(memory_root)
        self.clock = clock
        self.worker_types: dict[str, Callable[[], Worker]] = {
            "patrol": FakePatrolWorker,
        }
        self.events: list[WorkerEvent] = []
        self.results: dict[str, WorkerResult] = {}
        self.result_xml: dict[str, str] = {}
        self.updates: dict[str, list[dict[str, object]]] = {}
        self._tasks: dict[str, asyncio.Task[WorkerResult]] = {}
        self._tokens: dict[str, CancelToken] = {}

    def register_worker(self, task_type: str, factory: Callable[[], Worker]) -> None:
        """Register a worker type."""
        self.worker_types[task_type] = factory

    async def handle_decision(
        self,
        decision: dict[str, object] | None,
        *,
        parent_request_id: str,
    ) -> str | None:
        """Apply a Brain worker decision."""
        if not decision:
            return None
        op = str(decision.get("op", ""))
        task_id = str(decision.get("task_id", ""))
        if op == "spawn":
            task_type = str(decision.get("task_type", ""))
            params = dict(decision.get("task_params", {}) or {})
            await self.spawn(
                TaskSpec(
                    task_id=task_id,
                    task_type=task_type,
                    params=params,
                    parent_request_id=parent_request_id,
                    spawned_at=self.clock(),
                    deadline_s=None,
                    memory_namespace=task_id,
                )
            )
            return task_id
        if op == "update":
            if task_id not in self._tasks:
                raise ValueError(f"Cannot update unknown worker: {task_id}")
            params = dict(decision.get("task_params", {}) or {})
            self.updates.setdefault(task_id, []).append(params)
            self.events.append(
                WorkerEvent(
                    task_id=task_id,
                    event="progress",
                    payload={
                        "note": str(decision.get("summary", "worker updated") or ""),
                        "progress": None,
                        "metrics": {"updated": True},
                        "task_params": params,
                    },
                    ts_ms=int(self.clock() * 1000),
                )
            )
            return task_id
        if op == "cancel":
            self.cancel(task_id)
            return task_id
        raise ValueError(f"Unsupported worker decision op: {op}")

    async def spawn(self, spec: TaskSpec) -> asyncio.Task[WorkerResult]:
        """Spawn a worker and return its asyncio task."""
        if spec.task_type not in self.worker_types:
            raise ValueError(f"Unknown worker task_type: {spec.task_type}")
        if spec.task_id in self._tasks:
            raise ValueError(f"Worker already exists: {spec.task_id}")
        token = CancelToken()
        self._tokens[spec.task_id] = token
        worker = self.worker_types[spec.task_type]()
        context = WorkerContext(
            spec=spec,
            emit_action=self._worker_emit_action(spec),
            emit_event=self._worker_emit_event(spec),
            memory=WorkerMemory(self.memory_root, spec.memory_namespace),
            cancel_token=token,
            clock=self.clock,
        )
        task = asyncio.create_task(self._run_worker(worker, context))
        self._tasks[spec.task_id] = task
        return task

    def cancel(self, task_id: str) -> None:
        """Request cancellation for a running worker."""
        token = self._tokens.get(task_id)
        if token is not None:
            token.cancel()

    def is_running(self, task_id: str) -> bool:
        """Return whether a worker is still running."""
        task = self._tasks.get(task_id)
        return task is not None and not task.done()

    async def wait(self, task_id: str, *, timeout: float | None = None) -> WorkerResult:
        """Wait for one worker result."""
        task = self._tasks[task_id]
        return await asyncio.wait_for(task, timeout=timeout)

    def _worker_emit_action(
        self,
        spec: TaskSpec,
    ) -> Callable[[ActionSpec], Awaitable[ActionResult]]:
        async def emit(spec_action: ActionSpec) -> ActionResult:
            bounded_priority = min(spec_action.priority, 30)
            owned = ActionSpec(
                name=spec_action.name,
                params=spec_action.params,
                reason=spec_action.reason,
                request_id=spec_action.request_id,
                owner_id=f"worker:{spec.task_id}",
                priority=bounded_priority,
                interruptible=spec_action.interruptible,
                deadline_s=spec_action.deadline_s,
                parent_request_id=spec.parent_request_id,
            )
            return await self.emit_action(owned)

        return emit

    def _worker_emit_event(
        self,
        spec: TaskSpec,
    ) -> Callable[[str, dict[str, object]], Awaitable[None]]:
        async def emit(event: str, payload: dict[str, object]) -> None:
            frame = WorkerEvent(
                task_id=spec.task_id,
                event=event,
                payload=dict(payload),
                ts_ms=int(self.clock() * 1000),
            )
            self.events.append(frame)
            WorkerMemory(self.memory_root, spec.memory_namespace).append(
                {"type": "worker_event", "event": event, "payload": payload}
            )

        return emit

    async def _run_worker(self, worker: Worker, context: WorkerContext) -> WorkerResult:
        task_id = context.spec.task_id
        try:
            result = await worker.run(context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if context.cancel_token.is_cancelled:
                result = WorkerResult(
                    status="cancelled",
                    summary="worker cancelled",
                    metrics={"reason": type(exc).__name__},
                )
                await context.emit_event(
                    "cancelled",
                    {"note": "worker cancelled", "progress": None},
                )
            else:
                result = WorkerResult(
                    status="failed",
                    summary=str(exc),
                    metrics={"error": type(exc).__name__},
                )
                await context.emit_event(
                    "failed",
                    {"note": "worker failed", "error": str(exc), "progress": None},
                )
        self.results[task_id] = result
        self.result_xml[task_id] = worker_result_to_xml(result)
        await context.emit_event(
            result.status,
            {"note": result.summary, "progress": 1.0, "metrics": result.metrics},
        )
        return result
