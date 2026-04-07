"""Kairos dream runtime compatibility layer.

The durable consolidation implementation lives in ``services.auto_dream``.
This module keeps the older Kairos dream import surface while delegating to
that runtime and mirroring enough task state for callers to inspect progress.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..services.memdir import get_memory_dir as _service_get_memory_dir


def get_memory_dir(project_root: str | Path | None = None) -> Path:
    root = ""
    if project_root is not None:
        root = str(Path(project_root).resolve())
    return Path(_service_get_memory_dir(root))


def get_daily_log_path(memory_dir: Path | str | None = None, date: datetime | None = None) -> Path:
    mem = Path(memory_dir) if memory_dir is not None else get_memory_dir()
    ts = date or datetime.now(timezone.utc)
    return mem / "logs" / f"{ts:%Y}" / f"{ts:%m}" / f"{ts:%Y-%m-%d}.md"


class DailyLogWriter:
    def __init__(self, memory_dir: Path | str | None = None) -> None:
        self._memory_dir = Path(memory_dir) if memory_dir is not None else get_memory_dir()

    def append(self, entry: str, *, date: datetime | None = None) -> Path:
        path = get_daily_log_path(self._memory_dir, date)
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = (date or datetime.now(timezone.utc)).strftime("%H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"- [{stamp}] {entry}\n")
        return path


def build_consolidation_prompt(memory_dir: str, *, current_time: str = "") -> str:
    return (
        "Consolidate memory artifacts in "
        f"{memory_dir}. Current time: {current_time or 'unknown'}."
    )


def build_daily_log_prompt(memory_dir: str, *, skip_index: bool = False) -> str:
    return (
        f"Review daily logs in {memory_dir}."
        + (" Skip the index rebuild." if skip_index else "")
    )


class DreamPhase(str, Enum):
    STARTING = "starting"
    ORIENT = "orient"
    GATHER = "gather"
    CONSOLIDATE = "consolidate"
    PRUNE = "prune"
    COMPLETE = "complete"
    FAILED = "failed"
    KILLED = "killed"
    DISABLED = "disabled"


@dataclass(slots=True)
class DreamTurn:
    text: str
    tool_use_count: int = 0


@dataclass(slots=True)
class DreamTask:
    task_id: str
    status: str = "pending"
    phase: DreamPhase = DreamPhase.STARTING
    turns: list[DreamTurn] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    error: str = ""


_dream_tasks: dict[str, DreamTask] = {}

_PHASE_MAP = {
    "Orient": DreamPhase.ORIENT,
    "Gather": DreamPhase.GATHER,
    "Consolidate": DreamPhase.CONSOLIDATE,
    "Prune": DreamPhase.PRUNE,
}


def _normalise_task_id(task_id: str = "") -> str:
    return task_id or f"dream-{uuid4().hex[:12]}"


def _set_turns(task: DreamTask, texts: list[str]) -> None:
    seen: set[str] = set()
    turns: list[DreamTurn] = []
    for text in texts:
        item = str(text).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        turns.append(DreamTurn(text=item))
    task.turns = turns


def _format_touched_summary(touched: list[str]) -> str:
    if not touched:
        return "Dream completed with no memory file changes."
    if len(touched) == 1:
        return f"Dream updated 1 memory file: {Path(touched[0]).name}."
    names = ", ".join(Path(path).name for path in touched[:3])
    extra = f" (+{len(touched) - 3} more)" if len(touched) > 3 else ""
    return f"Dream updated {len(touched)} memory files: {names}{extra}."


def _sync_task_from_runtime(task: DreamTask, runtime_task: Any, *, touched: list[str] | None = None) -> None:
    status = getattr(runtime_task, "status", None)
    status_value = getattr(status, "value", str(status or "")).strip().lower()
    if status_value:
        task.status = status_value

    current_phase = str(getattr(runtime_task, "current_phase", "") or "").strip()
    if current_phase:
        task.phase = _PHASE_MAP.get(current_phase, task.phase)

    error = getattr(runtime_task, "_error", None)
    if error:
        task.error = str(error)

    runtime_turns = list(getattr(runtime_task, "_turns", []) or [])
    if touched is not None:
        runtime_turns.append(_format_touched_summary(touched))
    if runtime_turns:
        _set_turns(task, runtime_turns)


async def _mirror_runtime_progress(task: DreamTask, runtime_task: Any) -> None:
    while True:
        _sync_task_from_runtime(task, runtime_task)
        status = getattr(getattr(runtime_task, "status", None), "value", "")
        if status in {"complete", "failed"}:
            return
        await asyncio.sleep(0.05)


def register_dream(*, task_id: str) -> DreamTask:
    task = DreamTask(task_id=_normalise_task_id(task_id))
    _dream_tasks[task.task_id] = task
    return task


def get_dream_task(task_id: str) -> DreamTask | None:
    return _dream_tasks.get(task_id)


def list_dream_tasks() -> list[DreamTask]:
    return list(_dream_tasks.values())


def kill_dream(task_id: str, memory_dir: Path) -> bool:
    del memory_dir
    task = _dream_tasks.get(task_id)
    if task is None:
        return False
    try:
        from ..services.auto_dream import abort_consolidation

        abort_consolidation()
    except Exception:
        pass
    task.status = "killed"
    task.phase = DreamPhase.KILLED
    task.error = "dream aborted"
    task.end_time = time.time()
    return True


@dataclass(slots=True)
class NightlyDreamConfig:
    enabled: bool = False
    cron_expression: str = "0 3 * * *"


@dataclass(slots=True)
class GateCheckResult:
    allowed: bool
    reason: str = ""


def check_dream_gates(
    *args: Any,
    project_root: str | Path | None = None,
    memory_dir: str | Path | None = None,
    session_dir: str | Path | None = None,
    current_session: str = "",
    config: Any | None = None,
    enabled: bool | None = None,
    force: bool = False,
    **kwargs: Any,
) -> GateCheckResult:
    del args, kwargs
    from ..services.auto_dream import DEFAULT_CONFIG, is_forced, should_consolidate

    if force or is_forced():
        return GateCheckResult(True, "forced")

    if enabled is False:
        return GateCheckResult(False, "dream feature disabled")

    effective_memory_dir = str(memory_dir or get_memory_dir(project_root))
    effective_session_dir = str(session_dir or "")
    effective_config = config or DEFAULT_CONFIG
    allowed = should_consolidate(
        effective_config,
        memory_dir=effective_memory_dir,
        session_dir=effective_session_dir,
        current_session=current_session,
    )
    if allowed:
        return GateCheckResult(True, "")
    return GateCheckResult(False, "dream gates not satisfied")


async def run_nightly_dream(
    provider: Any,
    *args: Any,
    project_root: str | Path | None = None,
    memory_dir: str | Path | None = None,
    session_dir: str | Path | None = None,
    current_session: str = "",
    task_id: str = "",
    **kwargs: Any,
) -> str:
    del args, kwargs
    from ..services.auto_dream import DreamTask as RuntimeDreamTask
    from ..services.auto_dream import run_consolidation

    if provider is None:
        raise ValueError("provider is required to run dream consolidation")

    task = register_dream(task_id=_normalise_task_id(task_id))
    task.status = "running"
    task.phase = DreamPhase.STARTING
    task.error = ""
    task.start_time = time.time()
    task.end_time = 0.0

    runtime_task = RuntimeDreamTask(task_id=task.task_id)
    mirror_task = asyncio.create_task(_mirror_runtime_progress(task, runtime_task))

    try:
        effective_memory_dir = str(memory_dir or get_memory_dir(project_root))
        effective_project_root = str(Path(project_root).resolve()) if project_root is not None else ""
        effective_session_dir = str(session_dir or "")
        touched = await run_consolidation(
            provider,
            memory_dir=effective_memory_dir,
            project_root=effective_project_root,
            session_dir=effective_session_dir,
            current_session=current_session,
            task=runtime_task,
        )
        _sync_task_from_runtime(task, runtime_task, touched=list(touched))

        if task.status == "failed":
            task.phase = DreamPhase.FAILED
        else:
            task.status = "complete"
            task.phase = DreamPhase.COMPLETE
        task.end_time = time.time()
        return task.task_id
    except Exception as exc:
        task.status = "failed"
        task.phase = DreamPhase.FAILED
        task.error = str(exc)
        task.end_time = time.time()
        raise
    finally:
        mirror_task.cancel()
        try:
            await mirror_task
        except asyncio.CancelledError:
            pass


async def trigger_dream_from_kairos(
    provider: Any,
    *args: Any,
    project_root: str | Path | None = None,
    memory_dir: str | Path | None = None,
    session_dir: str | Path | None = None,
    current_session: str = "",
    task_id: str = "",
    config: Any | None = None,
    enabled: bool | None = None,
    force: bool = False,
    **kwargs: Any,
) -> bool:
    del args, kwargs
    if is_dream_running():
        return False

    gate_result = check_dream_gates(
        project_root=project_root,
        memory_dir=memory_dir,
        session_dir=session_dir,
        current_session=current_session,
        config=config,
        enabled=enabled,
        force=force,
    )
    if not gate_result.allowed:
        return False

    resolved_task_id = await run_nightly_dream(
        provider,
        project_root=project_root,
        memory_dir=memory_dir,
        session_dir=session_dir,
        current_session=current_session,
        task_id=task_id,
    )
    task = get_dream_task(resolved_task_id)
    return task is not None and task.status == "complete"


def record_consolidation(memory_dir: Path) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)


def create_dream_cron_task() -> dict[str, Any]:
    return {
        "name": "nightly_dream",
        "cron_expr": "0 3 * * *",
        "prompt": "Run nightly memory consolidation.",
        "task_type": "recurring",
    }


def is_dream_running() -> bool:
    return any(task.status == "running" for task in _dream_tasks.values())


@dataclass(slots=True)
class DreamScheduleConfig:
    cron_expression: str = "0 3 * * *"


def get_next_dream_time(
    config: DreamScheduleConfig | None = None,
    *,
    now: datetime | None = None,
) -> datetime:
    del config
    base = now or datetime.now(timezone.utc)
    next_run = base.replace(hour=3, minute=0, second=0, microsecond=0)
    if next_run <= base:
        next_run += timedelta(days=1)
    return next_run
