"""Worker runtime contracts for v4 long tasks."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from reachy_mini.action_runtime import ActionResult, ActionSpec, CancelToken

from .memory import WorkerMemory


@dataclass(frozen=True)
class TaskSpec:
    """Long-running worker task specification."""

    task_id: str
    task_type: str
    params: dict[str, Any]
    parent_request_id: str
    spawned_at: float
    deadline_s: float | None
    memory_namespace: str


@dataclass(frozen=True)
class WorkerResult:
    """Worker terminal result."""

    status: str
    summary: str
    memory_refs: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkerEvent:
    """Worker progress event."""

    task_id: str
    event: str
    payload: dict[str, Any]
    ts_ms: int


@dataclass
class WorkerContext:
    """Context object passed to worker implementations."""

    spec: TaskSpec
    emit_action: Callable[[ActionSpec], Awaitable[ActionResult]]
    emit_event: Callable[[str, dict[str, Any]], Awaitable[None]]
    memory: WorkerMemory
    cancel_token: CancelToken
    clock: Callable[[], float] = time.monotonic


class Worker(Protocol):
    """Protocol for v4 worker implementations."""

    async def run(self, ctx: WorkerContext) -> WorkerResult:
        """Run a worker task to completion."""
        ...


class FakePatrolWorker:
    """Small fake worker that emits head actions and progress."""

    async def run(self, ctx: WorkerContext) -> WorkerResult:
        """Emit deterministic patrol progress and action specs."""
        rounds = max(1, int(ctx.spec.params.get("rounds", 1)))
        memory_refs: list[str] = []
        await ctx.emit_event("started", {"note": "patrol started", "progress": 0.0})
        for index in range(rounds):
            await ctx.cancel_token.checkpoint()
            result = await ctx.emit_action(
                ActionSpec(
                    name="look_at",
                    params={"target": [1.0, 0.0, 0.2], "duration_s": 0.2},
                    reason="patrol scan",
                    request_id=f"{ctx.spec.task_id}:{index}",
                    owner_id=f"worker:{ctx.spec.task_id}",
                    priority=20,
                    parent_request_id=ctx.spec.parent_request_id,
                )
            )
            memory_refs.append(
                ctx.memory.append(
                    {
                        "type": "action_result",
                        "status": result.status,
                        "action": result.name,
                    }
                )
            )
            await ctx.emit_event(
                "progress",
                {
                    "note": "patrol progress",
                    "progress": (index + 1) / rounds,
                    "metrics": {"actions": index + 1},
                },
            )
            await asyncio.sleep(0.05)
        result = WorkerResult(
            status="completed",
            summary="patrol completed",
            memory_refs=memory_refs,
            metrics={"rounds": rounds},
        )
        ctx.memory.append(
            {
                "type": "worker_result",
                "status": result.status,
                "summary": result.summary,
                "memory_refs": result.memory_refs,
                "metrics": result.metrics,
            }
        )
        return result


def worker_result_to_xml(result: WorkerResult) -> str:
    """Serialize a worker result for model context injection."""
    refs = "".join(f"<ref>{ref}</ref>" for ref in result.memory_refs)
    return (
        "<task-result>"
        f"<status>{result.status}</status>"
        f"<summary>{result.summary}</summary>"
        f"<memory_refs>{refs}</memory_refs>"
        "</task-result>"
    )


def memory_root_for(base: Path | None) -> Path:
    """Resolve worker memory root."""
    return base if base is not None else Path(".reachy_v4_memory")
