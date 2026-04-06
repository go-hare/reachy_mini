"""Dream feature shim.

The recovered reference tree contains auto-dream functionality in services,
but no standalone Kairos dream runtime module. Keep only compatibility hooks
needed by the Python host, with the feature disabled by default.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any


def get_memory_dir(project_root: str | Path | None = None) -> Path:
    root = Path(project_root) if project_root is not None else Path.cwd()
    return root / ".mini_agent" / "memory"


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
    DISABLED = "disabled"


@dataclass(slots=True)
class DreamTurn:
    text: str
    tool_use_count: int = 0


@dataclass(slots=True)
class DreamTask:
    task_id: str
    status: str = "disabled"
    phase: DreamPhase = DreamPhase.DISABLED
    turns: list[DreamTurn] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    error: str = "dream runtime is unavailable in this recovered build"


_dream_tasks: dict[str, DreamTask] = {}


def register_dream(*, task_id: str) -> DreamTask:
    task = DreamTask(task_id=task_id)
    _dream_tasks[task_id] = task
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
    task.status = "killed"
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


def check_dream_gates(*args: Any, **kwargs: Any) -> GateCheckResult:
    del args, kwargs
    return GateCheckResult(False, "dream runtime is unavailable in this recovered build")


async def run_nightly_dream(*args: Any, **kwargs: Any) -> str:
    del args, kwargs
    raise RuntimeError("dream runtime is unavailable in this recovered build")


async def trigger_dream_from_kairos(*args: Any, **kwargs: Any) -> bool:
    del args, kwargs
    return False


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

