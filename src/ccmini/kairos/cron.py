"""Cron scheduler — schedule and execute recurring/one-shot tasks.

Port of Claude Code's cronScheduler.ts + cronTasks.ts + ScheduleCronTool/.
Provides a lightweight cron engine with:
- Standard cron expressions (minute hour dom month dow)
- One-shot and recurring tasks
- Jitter to spread load on round times
- Durable persistence to ~/.mini_agent/scheduled_tasks.json
- Kill switch and per-task gates
- Missed-fire detection for recurring tasks
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Awaitable

from ..paths import mini_agent_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cron expression parser (minimal 5-field: min hour dom month dow)
# ---------------------------------------------------------------------------

_FIELD_RANGES = [
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 6),    # day of week (0=Sun)
]


def _parse_field(token: str, lo: int, hi: int) -> set[int]:
    """Parse a single cron field into a set of matching values."""
    result: set[int] = set()
    for part in token.split(","):
        step_parts = part.split("/", 1)
        range_part = step_parts[0]
        step = int(step_parts[1]) if len(step_parts) > 1 else 1

        if range_part == "*":
            result.update(range(lo, hi + 1, step))
        elif "-" in range_part:
            a, b = range_part.split("-", 1)
            result.update(range(int(a), int(b) + 1, step))
        else:
            result.add(int(range_part))
    return {v for v in result if lo <= v <= hi}


@dataclass(frozen=True)
class CronExpr:
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    raw: str

    @classmethod
    def parse(cls, expr: str) -> CronExpr:
        tokens = expr.strip().split()
        if len(tokens) != 5:
            raise ValueError(f"Expected 5 fields, got {len(tokens)}: {expr!r}")
        fields = []
        for tok, (lo, hi) in zip(tokens, _FIELD_RANGES):
            fields.append(frozenset(_parse_field(tok, lo, hi)))
        return cls(*fields, raw=expr)

    def matches(self, dt: datetime) -> bool:
        return (
            dt.minute in self.minutes
            and dt.hour in self.hours
            and dt.day in self.days_of_month
            and dt.month in self.months
            and dt.weekday() in self._py_dow()
        )

    def _py_dow(self) -> frozenset[int]:
        """Convert cron dow (0=Sun) to Python weekday (0=Mon)."""
        mapping = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
        return frozenset(mapping.get(d, d) for d in self.days_of_week)

    def next_fire(self, after: datetime) -> datetime | None:
        """Brute-force search for next matching minute (up to 400 days)."""
        dt = after.replace(second=0, microsecond=0)
        from datetime import timedelta
        dt += timedelta(minutes=1)
        limit = 400 * 24 * 60
        for _ in range(limit):
            if self.matches(dt):
                return dt
            dt += timedelta(minutes=1)
        return None


# ---------------------------------------------------------------------------
# Jitter config (mirrors cronJitterConfig.ts / CronJitterConfig)
# ---------------------------------------------------------------------------

@dataclass
class CronJitterConfig:
    recurring_frac: float = 0.1
    recurring_cap_ms: int = 60_000
    one_shot_max_ms: int = 90_000
    one_shot_floor_ms: int = 0
    one_shot_minute_mod: int = 30
    recurring_max_age_ms: int = 7 * 24 * 60 * 60 * 1000  # 7 days

DEFAULT_JITTER = CronJitterConfig()


def compute_one_shot_jitter(
    fire_time: datetime,
    cfg: CronJitterConfig = DEFAULT_JITTER,
) -> float:
    """Random jitter in seconds for one-shot tasks on round minutes."""
    if fire_time.minute % cfg.one_shot_minute_mod != 0:
        return 0.0
    jitter_ms = random.uniform(cfg.one_shot_floor_ms, cfg.one_shot_max_ms)
    return jitter_ms / 1000.0


def compute_recurring_jitter(
    interval_ms: float,
    cfg: CronJitterConfig = DEFAULT_JITTER,
) -> float:
    """Forward delay as a fraction of the interval for recurring tasks."""
    jitter_ms = min(interval_ms * cfg.recurring_frac, cfg.recurring_cap_ms)
    return random.uniform(0, jitter_ms) / 1000.0


# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------

class TaskType(str, Enum):
    ONE_SHOT = "one_shot"
    RECURRING = "recurring"


@dataclass
class CronTask:
    id: str
    name: str
    cron_expr: str
    prompt: str                     # The prompt/instruction to execute
    task_type: TaskType = TaskType.RECURRING
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    last_fired_at: float = 0.0
    fire_count: int = 0
    max_fires: int = 0             # 0 = unlimited
    metadata: dict[str, Any] = field(default_factory=dict)

    _parsed: CronExpr | None = field(default=None, repr=False, compare=False)

    @property
    def parsed_expr(self) -> CronExpr:
        if self._parsed is None:
            self._parsed = CronExpr.parse(self.cron_expr)
        return self._parsed

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "cron_expr": self.cron_expr,
            "prompt": self.prompt,
            "task_type": self.task_type.value,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "last_fired_at": self.last_fired_at,
            "fire_count": self.fire_count,
            "max_fires": self.max_fires,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronTask:
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            cron_expr=data["cron_expr"],
            prompt=data["prompt"],
            task_type=TaskType(data.get("task_type", "recurring")),
            enabled=data.get("enabled", True),
            created_at=data.get("created_at", time.time()),
            last_fired_at=data.get("last_fired_at", 0.0),
            fire_count=data.get("fire_count", 0),
            max_fires=data.get("max_fires", 0),
            metadata=data.get("metadata", {}),
        )


def generate_task_id(name: str) -> str:
    ts = str(time.time_ns())
    return hashlib.sha256(f"{name}:{ts}".encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Persistent task storage (~/.mini_agent/scheduled_tasks.json)
# ---------------------------------------------------------------------------

class TaskStore:
    """Read/write scheduled tasks to a JSON file."""

    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            path = mini_agent_path("scheduled_tasks.json")
        self._path = Path(path)

    def load(self) -> list[CronTask]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return [CronTask.from_dict(t) for t in data.get("tasks", [])]
        except (json.JSONDecodeError, KeyError):
            logger.warning("Corrupt task store at %s, resetting", self._path)
            return []

    def save(self, tasks: list[CronTask]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated_at": time.time(),
            "tasks": [t.to_dict() for t in tasks],
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def add(self, task: CronTask) -> None:
        tasks = self.load()
        tasks = [t for t in tasks if t.id != task.id]
        tasks.append(task)
        self.save(tasks)

    def remove(self, task_id: str) -> bool:
        tasks = self.load()
        new_tasks = [t for t in tasks if t.id != task_id]
        if len(new_tasks) == len(tasks):
            return False
        self.save(new_tasks)
        return True

    def update(self, task: CronTask) -> None:
        self.add(task)


# ---------------------------------------------------------------------------
# Cron scheduler engine (mirrors cronScheduler.ts)
# ---------------------------------------------------------------------------

CronCallback = Callable[[CronTask], Awaitable[None]]


@dataclass
class CronSchedulerConfig:
    check_interval_s: float = 60.0
    jitter: CronJitterConfig = field(default_factory=CronJitterConfig)
    max_age_ms: float = 7 * 24 * 60 * 60 * 1000
    is_killed: Callable[[], bool] | None = None
    task_gate: Callable[[CronTask], bool] | None = None


class CronScheduler:
    """Async cron scheduler that checks tasks every tick.

    Usage::

        scheduler = CronScheduler(store, config)
        await scheduler.start(on_fire=my_callback)
        # ... later ...
        scheduler.stop()
    """

    def __init__(
        self,
        store: TaskStore,
        config: CronSchedulerConfig | None = None,
    ) -> None:
        self._store = store
        self._config = config or CronSchedulerConfig()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._running

    async def start(self, on_fire: CronCallback) -> None:
        """Start the scheduler loop."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(on_fire))

    def stop(self) -> None:
        """Signal the scheduler to stop."""
        self._running = False
        self._stop_event.set()

    async def wait(self) -> None:
        if self._task:
            await self._task

    async def check_now(self, on_fire: CronCallback) -> int:
        """Run a single check cycle immediately. Returns number of tasks fired."""
        return await self._check(on_fire)

    async def _loop(self, on_fire: CronCallback) -> None:
        logger.info("Cron scheduler started (interval=%.0fs)", self._config.check_interval_s)
        try:
            while not self._stop_event.is_set():
                # Kill switch
                if self._config.is_killed and self._config.is_killed():
                    logger.warning("Cron kill switch activated")
                    break

                fired = await self._check(on_fire)
                if fired > 0:
                    logger.debug("Cron: fired %d task(s)", fired)

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._config.check_interval_s,
                    )
                    break
                except asyncio.TimeoutError:
                    pass
        finally:
            self._running = False
            logger.info("Cron scheduler stopped")

    async def _check(self, on_fire: CronCallback) -> int:
        now = datetime.now(timezone.utc)
        now_ts = time.time()
        tasks = self._store.load()
        fired = 0

        for task in tasks:
            if not task.enabled:
                continue
            if task.max_fires > 0 and task.fire_count >= task.max_fires:
                continue
            if self._config.task_gate and not self._config.task_gate(task):
                continue

            # Check age limit for recurring tasks
            if task.task_type == TaskType.RECURRING:
                age_ms = (now_ts - task.created_at) * 1000
                if age_ms > self._config.max_age_ms:
                    logger.debug("Cron task %s expired (age=%.0fms)", task.id, age_ms)
                    task.enabled = False
                    self._store.update(task)
                    continue

            # Check if the cron expression matches now
            try:
                expr = task.parsed_expr
            except ValueError:
                logger.warning("Invalid cron expr for task %s: %s", task.id, task.cron_expr)
                continue

            if not expr.matches(now):
                continue

            # Dedup: don't fire if already fired this minute
            if task.last_fired_at > 0:
                last_fire_dt = datetime.fromtimestamp(task.last_fired_at, tz=timezone.utc)
                if (
                    last_fire_dt.year == now.year
                    and last_fire_dt.month == now.month
                    and last_fire_dt.day == now.day
                    and last_fire_dt.hour == now.hour
                    and last_fire_dt.minute == now.minute
                ):
                    continue

            # Apply jitter
            if task.task_type == TaskType.ONE_SHOT:
                jitter = compute_one_shot_jitter(now, self._config.jitter)
            else:
                interval_ms = self._config.check_interval_s * 1000
                jitter = compute_recurring_jitter(interval_ms, self._config.jitter)

            if jitter > 0:
                await asyncio.sleep(jitter)

            # Fire!
            task.last_fired_at = time.time()
            task.fire_count += 1
            self._store.update(task)

            try:
                await on_fire(task)
                fired += 1
            except Exception:
                logger.exception("Error firing cron task %s", task.id)

            # Disable one-shot after firing
            if task.task_type == TaskType.ONE_SHOT:
                task.enabled = False
                self._store.update(task)

        return fired


# ---------------------------------------------------------------------------
# Convenience API (CronCreate / CronDelete / CronList tool equivalents)
# ---------------------------------------------------------------------------

def create_cron_task(
    store: TaskStore,
    *,
    name: str,
    cron_expr: str,
    prompt: str,
    task_type: str = "recurring",
    max_fires: int = 0,
    metadata: dict[str, Any] | None = None,
) -> CronTask:
    CronExpr.parse(cron_expr)  # validate
    task = CronTask(
        id=generate_task_id(name),
        name=name,
        cron_expr=cron_expr,
        prompt=prompt,
        task_type=TaskType(task_type),
        max_fires=max_fires,
        metadata=metadata or {},
    )
    store.add(task)
    return task


def delete_cron_task(store: TaskStore, task_id: str) -> bool:
    return store.remove(task_id)


def list_cron_tasks(store: TaskStore) -> list[CronTask]:
    return store.load()


# ---------------------------------------------------------------------------
# Scheduler lock — file-based multi-instance lock with stale PID detection
# ---------------------------------------------------------------------------

import os as _os
import signal

_CRON_LOCK_PATH = mini_agent_path("cron.lock")


class SchedulerLock:
    """File-based lock ensuring only one cron scheduler instance runs.

    Writes the current PID to ``~/.mini_agent/cron.lock``. On acquire,
    checks for a live holder (stale lock detection via PID liveness).
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else _CRON_LOCK_PATH
        self._held = False

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            _os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def acquire(self) -> bool:
        """Attempt to acquire the lock. Returns True on success."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        if self._path.exists():
            try:
                holder_pid = int(self._path.read_text(encoding="utf-8").strip())
                if self._pid_alive(holder_pid):
                    logger.debug("Cron lock held by live PID %d", holder_pid)
                    return False
                logger.debug("Cron lock stale (PID %d dead), stealing", holder_pid)
            except (ValueError, OSError):
                pass

        my_pid = str(_os.getpid())
        self._path.write_text(my_pid, encoding="utf-8")

        try:
            verify = self._path.read_text(encoding="utf-8").strip()
            if verify != my_pid:
                return False
        except OSError:
            return False

        self._held = True
        return True

    def release(self) -> None:
        if self._held:
            try:
                if self._path.exists():
                    current = self._path.read_text(encoding="utf-8").strip()
                    if current == str(_os.getpid()):
                        self._path.unlink(missing_ok=True)
            except OSError:
                pass
            self._held = False

    @property
    def is_held(self) -> bool:
        return self._held

    def __enter__(self) -> SchedulerLock:
        if not self.acquire():
            raise RuntimeError("Failed to acquire cron scheduler lock")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


# ---------------------------------------------------------------------------
# Missed task detection
# ---------------------------------------------------------------------------

def _detect_missed_tasks(
    store: TaskStore,
    cron_state: dict[str, float] | None = None,
) -> list[CronTask]:
    """Check for tasks that should have fired while the scheduler was offline.

    Compares each task's last-fired-at (or its entry in persistent cron state)
    against its cron expression to find missed windows.
    """
    now = datetime.now(timezone.utc)
    now_ts = time.time()
    tasks = store.load()
    missed: list[CronTask] = []
    state = cron_state or {}

    for task in tasks:
        if not task.enabled:
            continue
        if task.max_fires > 0 and task.fire_count >= task.max_fires:
            continue

        last_run = state.get(task.id, task.last_fired_at)
        if last_run <= 0:
            continue

        try:
            expr = task.parsed_expr
        except ValueError:
            continue

        last_dt = datetime.fromtimestamp(last_run, tz=timezone.utc)
        next_fire = expr.next_fire(last_dt)
        if next_fire is not None and next_fire < now:
            missed.append(task)

    return missed


# ---------------------------------------------------------------------------
# Persistent cron state — last-run timestamps
# ---------------------------------------------------------------------------

_CRON_STATE_PATH = mini_agent_path("cron_state.json")


class CronStateStore:
    """Persists per-task last-run timestamps for missed-task detection."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else _CRON_STATE_PATH

    def load(self) -> dict[str, float]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return {k: float(v) for k, v in data.get("last_run", {}).items()}
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, last_run: dict[str, float]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "updated_at": time.time(), "last_run": last_run}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def record_run(self, task_id: str, ts: float | None = None) -> None:
        state = self.load()
        state[task_id] = ts or time.time()
        self.save(state)

    def get_last_run(self, task_id: str) -> float:
        return self.load().get(task_id, 0.0)

    def detect_missed(self, store: TaskStore) -> list[CronTask]:
        return _detect_missed_tasks(store, self.load())


# ---------------------------------------------------------------------------
# Session cron jobs — only run during active sessions
# ---------------------------------------------------------------------------

SessionCronCallback = Callable[[], Awaitable[None]]


@dataclass
class SessionCronJob:
    name: str
    interval_s: float
    callback: SessionCronCallback
    last_run_ts: float = 0.0
    active: bool = True


class SessionCronManager:
    """Manages cron jobs that only fire during an active session."""

    def __init__(self) -> None:
        self._jobs: dict[str, SessionCronJob] = {}
        self._session_active = False

    def add_session_job(
        self,
        name: str,
        interval_s: float,
        callback: SessionCronCallback,
    ) -> None:
        self._jobs[name] = SessionCronJob(
            name=name, interval_s=interval_s, callback=callback,
        )

    def remove_session_job(self, name: str) -> bool:
        return self._jobs.pop(name, None) is not None

    def start_session(self) -> None:
        self._session_active = True
        for job in self._jobs.values():
            job.active = True

    def end_session(self) -> None:
        self._session_active = False
        for job in self._jobs.values():
            job.active = False

    async def tick(self) -> int:
        """Check and fire due session jobs. Returns count of jobs fired."""
        if not self._session_active:
            return 0
        now = time.time()
        fired = 0
        for job in self._jobs.values():
            if not job.active:
                continue
            if (now - job.last_run_ts) >= job.interval_s:
                job.last_run_ts = now
                try:
                    await job.callback()
                    fired += 1
                except Exception:
                    logger.exception("Session cron job %s failed", job.name)
        return fired

    def list_jobs(self) -> list[SessionCronJob]:
        return list(self._jobs.values())


# ---------------------------------------------------------------------------
# Cron expression helpers — parse_cron_expression convenience
# ---------------------------------------------------------------------------

def parse_cron_expression(expr: str) -> datetime | None:
    """Parse a cron expression and return the next fire time from now.

    Convenience wrapper around CronExpr.parse + next_fire.

    Examples::

        parse_cron_expression("*/5 * * * *")  # every 5 minutes
        parse_cron_expression("0 2 * * *")    # daily at 2am
    """
    parsed = CronExpr.parse(expr)
    return parsed.next_fire(datetime.now(timezone.utc))
