"""AutoDream — background memory consolidation.

Ported from Claude Code's ``autoDream`` subsystem:
- Periodically reviews accumulated session transcripts
- Consolidates and improves memory files
- Gate order (cheapest first): time → session count → lock
- Default: runs after 24h with 5+ sessions since last consolidation

This is the "defrag" for the memory system — it merges related
memories, removes stale ones, and fills gaps.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

from ..hooks import PostSamplingHook
from ..messages import Message
from ..tool import Tool, ToolUseContext
from .memdir import (
    format_memory_manifest,
    get_memory_dir,
    scan_memory_files,
)

if TYPE_CHECKING:
    from ..providers import BaseProvider
    from ..hooks import PostSamplingContext

logger = logging.getLogger(__name__)


class _MemoryActionTool(Tool):
    """Restricted tool for consolidation file actions inside the memory dir."""

    name = "memory_action"
    description = "Create, update, or delete a memory file inside the memory directory."
    is_read_only = False

    def __init__(self, memory_dir: str) -> None:
        self._memory_dir = Path(memory_dir).resolve()
        self.touched_paths: list[str] = []

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "update", "delete"]},
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["action", "path"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        action = str(kwargs.get("action", "")).strip()
        relative = str(kwargs.get("path", "")).strip()
        content = str(kwargs.get("content", ""))
        if not action or not relative:
            return "Error: action and path are required."

        target = (self._memory_dir / relative).resolve()
        if not str(target).startswith(str(self._memory_dir)):
            return f"Error: Access denied. Path must stay inside {self._memory_dir}"

        try:
            if action in {"create", "update"}:
                if not content:
                    return "Error: content is required for create/update."
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            elif action == "delete":
                if target.exists():
                    target.unlink()
            else:
                return f"Error: Unsupported action: {action}"
        except OSError as exc:
            return f"Error applying memory action: {exc}"

        path_text = str(target)
        if path_text not in self.touched_paths:
            self.touched_paths.append(path_text)
        return f"{action} {target}"


# ── Configuration ───────────────────────────────────────────────────

@dataclass(slots=True)
class AutoDreamConfig:
    """Scheduling knobs for memory consolidation."""

    min_hours: float = 24.0
    min_sessions: int = 5
    scan_interval_seconds: float = 600.0  # 10 minutes
    enabled: bool = True


DEFAULT_CONFIG = AutoDreamConfig()


# ── State ───────────────────────────────────────────────────────────

@dataclass
class AutoDreamState:
    """Mutable state for the auto-dream system."""

    last_consolidated_at: float = 0.0
    last_scan_at: float = 0.0
    in_progress: bool = False
    consolidation_count: int = 0
    files_touched: list[str] = field(default_factory=list)


_state = AutoDreamState()


def get_auto_dream_state() -> AutoDreamState:
    return _state


def reset_auto_dream_state() -> None:
    global _state
    _state = AutoDreamState()


# ── Lock management (PID-based competition) ─────────────────────────

LOCK_FILE = ".consolidate-lock"
HOLDER_STALE_S = 3600  # 1 hour — stale even if PID is live (PID reuse guard)


def _get_lock_path(memory_dir: str) -> Path:
    return Path(memory_dir) / LOCK_FILE


def _read_last_consolidated(memory_dir: str) -> float:
    """Read the last consolidation timestamp from the lock file (mtime)."""
    lock_path = _get_lock_path(memory_dir)
    if lock_path.exists():
        try:
            return lock_path.stat().st_mtime
        except OSError:
            pass
    return 0.0


def _is_process_alive(pid: int) -> bool:
    """Check whether *pid* is a running process."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, just not ours
    except OSError:
        return False


def _verify_lock_owner(lock_path: Path) -> int | None:
    """Read the holder PID from the lock body. Returns PID or None."""
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
        pid = int(raw.split("\n", 1)[0])
        return pid if pid > 0 else None
    except (OSError, ValueError):
        return None


def _try_acquire_lock(memory_dir: str) -> float | None:
    """Acquire the consolidation lock (PID-based).

    Writes ``{pid}\\n{timestamp}`` to the lock file.  If another process
    holds it and is still alive (verified via ``os.kill(pid, 0)``), returns
    ``None``.  If the holder is dead, steals the lock.

    Returns the prior mtime (for rollback) or ``None`` if blocked.
    """
    lock_path = _get_lock_path(memory_dir)

    mtime: float | None = None
    holder_pid: int | None = None
    if lock_path.exists():
        try:
            mtime = lock_path.stat().st_mtime
        except OSError:
            pass
        holder_pid = _verify_lock_owner(lock_path)

    if mtime is not None and (time.time() - mtime) < HOLDER_STALE_S:
        if holder_pid is not None and _is_process_alive(holder_pid):
            logger.debug(
                "Lock held by live PID %d (age %.0fs)",
                holder_pid, time.time() - mtime,
            )
            return None
        # Dead PID or unparseable body — reclaim.

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(f"{os.getpid()}\n{time.time()}", encoding="utf-8")

    # Two reclaimers both write → last writer wins. Loser bails on re-read.
    verify_pid = _verify_lock_owner(lock_path)
    if verify_pid != os.getpid():
        return None

    return mtime if mtime is not None else 0.0


def _rollback_lock(memory_dir: str, prior_mtime: float) -> None:
    """Rewind lock to pre-acquire state after a failed consolidation.

    Clears the PID body so our still-running process doesn't look like
    it's holding.  prior_mtime == 0 → unlink (restore no-file state).
    """
    lock_path = _get_lock_path(memory_dir)
    try:
        if prior_mtime == 0:
            if lock_path.exists():
                lock_path.unlink()
            return
        lock_path.write_text("", encoding="utf-8")
        os.utime(str(lock_path), (prior_mtime, prior_mtime))
    except OSError as exc:
        logger.debug("Lock rollback failed: %s — next trigger delayed", exc)


# ── Session listing ──────────────────────────────────────────────────

@dataclass(slots=True)
class SessionInfo:
    """Metadata for a session transcript touched since last consolidation."""

    session_id: str
    path: str
    mtime: float
    message_count: int


def _estimate_message_count(fp: Path) -> int:
    """Rough line/object count in a JSONL transcript (one JSON per line)."""
    try:
        return sum(1 for line in fp.open(encoding="utf-8") if line.strip())
    except OSError:
        return 0


def _list_sessions_since(
    session_dir: str,
    since_time: float,
    current_session: str = "",
) -> list[SessionInfo]:
    """List session transcripts modified since *since_time*.

    Returns ``SessionInfo`` objects (used by the consolidation prompt to
    know which sessions to review).  The old ``_count_sessions_since``
    behaviour is recovered via ``len(_list_sessions_since(...))``.
    """
    session_path = Path(session_dir)
    if not session_path.is_dir():
        return []

    results: list[SessionInfo] = []
    try:
        for fp in session_path.iterdir():
            if not fp.is_file():
                continue
            # Transcripts are ``<session_id>.jsonl``; compare stem to id.
            if current_session and (fp.stem == current_session or fp.name == current_session):
                continue
            try:
                mtime = fp.stat().st_mtime
            except OSError:
                continue
            if mtime > since_time:
                results.append(SessionInfo(
                    session_id=fp.stem,
                    path=str(fp),
                    mtime=mtime,
                    message_count=_estimate_message_count(fp),
                ))
    except OSError:
        pass

    results.sort(key=lambda s: s.mtime, reverse=True)
    return results


def _count_sessions_since(
    session_dir: str,
    since_time: float,
    current_session: str = "",
) -> int:
    """Backwards-compatible count wrapper around ``_list_sessions_since``."""
    return len(_list_sessions_since(session_dir, since_time, current_session))


# ── Consolidation prompt (4-phase) ──────────────────────────────────

ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200

DIR_EXISTS_GUIDANCE = (
    "If the memory directory already has content, read MEMORY.md first to "
    "orient. If the directory is empty, create MEMORY.md as the index file."
)


def _build_consolidation_prompt(
    memory_dir: str,
    existing_memories: str,
    session_count: int,
    *,
    transcript_dir: str = "",
    session_ids: list[str] | None = None,
    extra: str = "",
) -> str:
    """Build the full 4-phase consolidation prompt.

    Ported from Claude Code's ``consolidationPrompt.ts``.  Each phase maps
    to a distinct reasoning step that the model should follow:

    1. ORIENT  — read the current MEMORY.md index, understand structure
    2. GATHER  — search daily logs and recent transcripts for new insights
    3. CONSOLIDATE — merge new signal into existing memories, update index
    4. PRUNE   — remove outdated/duplicate memories, keep index clean
    """
    sessions_section = ""
    if session_ids:
        session_list = "\n".join(f"- {sid}" for sid in session_ids)
        sessions_section = (
            f"\n\nSessions since last consolidation ({len(session_ids)}):\n"
            f"{session_list}"
        )

    transcript_ref = (
        f"\n\nSession transcripts: `{transcript_dir}` "
        "(large JSONL files — grep narrowly, don't read whole files)"
        if transcript_dir else ""
    )

    extra_section = f"\n\n## Additional context\n\n{extra}" if extra else ""

    return f"""\
# Dream: Memory Consolidation

You are performing a dream — a reflective pass over your memory files. \
Synthesize what you've learned recently into durable, well-organized \
memories so that future sessions can orient quickly.

Memory directory: `{memory_dir}`
{DIR_EXISTS_GUIDANCE}
{transcript_ref}

## Current memory files
{existing_memories if existing_memories else "(empty — no memories yet)"}

---

## Phase 1 — Orient

- `ls` the memory directory to see what already exists
- Read `{ENTRYPOINT_NAME}` to understand the current index
- Skim existing topic files so you improve them rather than creating duplicates
- If `logs/` or `sessions/` subdirectories exist (assistant-mode layout), \
review recent entries there

## Phase 2 — Gather recent signal

Look for new information worth persisting. Sources in rough priority order:

1. **Daily logs** (`logs/YYYY/MM/YYYY-MM-DD.md`) if present — these are the \
append-only stream
2. **Existing memories that drifted** — facts that contradict something you \
see in the codebase now
3. **Transcript search** — if you need specific context (e.g., "what was the \
error message from yesterday's build failure?"), grep the JSONL transcripts \
for narrow terms

Don't exhaustively read transcripts. Look only for things you already suspect \
matter.

## Phase 3 — Consolidate

For each thing worth remembering, write or update a memory file at the top \
level of the memory directory.

Focus on:
- Merging new signal into existing topic files rather than creating \
near-duplicates
- Converting relative dates ("yesterday", "last week") to absolute dates so \
they remain interpretable after time passes
- Deleting contradicted facts — if today's investigation disproves an old \
memory, fix it at the source

## Phase 4 — Prune and index

Update `{ENTRYPOINT_NAME}` so it stays under {MAX_ENTRYPOINT_LINES} lines \
AND under ~25KB. It's an **index**, not a dump — each entry should be one \
line under ~150 characters: `- [Title](file.md) — one-line hook`. Never \
write memory content directly into it.

- Remove pointers to memories that are now stale, wrong, or superseded
- Demote verbose entries: if an index line is over ~200 chars, it's carrying \
content that belongs in the topic file — shorten the line, move the detail
- Add pointers to newly important memories
- Resolve contradictions — if two files disagree, fix the wrong one

---

{session_count} sessions have accumulated since the last consolidation.
{sessions_section}

## Output

Respond with a JSON array of actions:
```json
[
  {{"action": "update", "path": "existing_file.md", "content": "updated content"}},
  {{"action": "create", "path": "new_file.md", "content": "new content"}},
  {{"action": "delete", "path": "obsolete_file.md"}}
]
```

If no changes are needed, respond with an empty array [].

Return a brief summary of what you consolidated, updated, or pruned. If \
nothing changed (memories are already tight), say so.{extra_section}"""


# ── DreamTask progress tracking ──────────────────────────────────────

class DreamTaskStatus(enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class DreamTask:
    """Tracks progress of a single consolidation run.

    Used by the ``DreamProgressWatcher`` and UI to show phase/percentage.
    """

    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: DreamTaskStatus = DreamTaskStatus.PENDING
    progress_pct: int = 0
    turns_processed: int = 0
    total_turns: int = 0
    current_phase: str = ""
    _turns: list[str] = field(default_factory=list, repr=False)
    _error: str | None = field(default=None, repr=False)

    def add_turn(self, text: str) -> None:
        """Record a consolidation turn (e.g. an assistant response)."""
        self._turns.append(text)
        self.turns_processed = len(self._turns)
        if self.total_turns > 0:
            self.progress_pct = min(
                95, int(self.turns_processed / self.total_turns * 100),
            )

    def complete(self) -> None:
        self.status = DreamTaskStatus.COMPLETE
        self.progress_pct = 100

    def fail(self, error: str) -> None:
        self.status = DreamTaskStatus.FAILED
        self._error = error

    def get_progress_summary(self) -> str:
        """Human-readable progress, e.g. 'Phase 2/4: Gathering insights (45%)'."""
        phase_map = {
            "Orient": (1, "Reading memory index"),
            "Gather": (2, "Gathering insights"),
            "Consolidate": (3, "Consolidating memories"),
            "Prune": (4, "Pruning and indexing"),
        }
        info = phase_map.get(self.current_phase)
        if info:
            num, desc = info
            return f"Phase {num}/4: {desc} ({self.progress_pct}%)"
        if self.status == DreamTaskStatus.COMPLETE:
            return "Consolidation complete (100%)"
        if self.status == DreamTaskStatus.FAILED:
            return f"Consolidation failed: {self._error or 'unknown'}"
        return f"Consolidating… ({self.progress_pct}%)"


# ── Dream progress watcher ──────────────────────────────────────────

@dataclass(slots=True)
class DreamProgressEvent:
    """A progress update yielded by ``DreamProgressWatcher``."""

    task_id: str
    status: DreamTaskStatus
    progress_pct: int
    summary: str


class DreamProgressWatcher:
    """Background watcher that yields progress events for a ``DreamTask``.

    Usage::

        watcher = DreamProgressWatcher(task, interval=2.0)
        async for event in watcher.watch():
            display(event.summary)
    """

    def __init__(self, task: DreamTask, *, interval: float = 2.0) -> None:
        self._task = task
        self._interval = interval

    async def watch(self) -> AsyncIterator[DreamProgressEvent]:
        """Periodically yield progress events until the task finishes."""
        last_pct = -1
        while self._task.status in (
            DreamTaskStatus.PENDING, DreamTaskStatus.RUNNING,
        ):
            if self._task.progress_pct != last_pct:
                last_pct = self._task.progress_pct
                yield DreamProgressEvent(
                    task_id=self._task.task_id,
                    status=self._task.status,
                    progress_pct=self._task.progress_pct,
                    summary=self._task.get_progress_summary(),
                )
            await asyncio.sleep(self._interval)

        # Final event
        yield DreamProgressEvent(
            task_id=self._task.task_id,
            status=self._task.status,
            progress_pct=self._task.progress_pct,
            summary=self._task.get_progress_summary(),
        )


def make_dream_progress_watcher(
    task: DreamTask,
    *,
    interval: float = 2.0,
) -> DreamProgressWatcher:
    """Factory for creating a progress watcher for a dream task."""
    return DreamProgressWatcher(task, interval=interval)


# ── Force dream ─────────────────────────────────────────────────────

def is_forced() -> bool:
    """Check ``MINI_AGENT_FORCE_DREAM=1`` for testing.

    Bypasses enabled/time/session gates but NOT the lock (so repeated
    turns don't pile up dreams).
    """
    return os.environ.get("MINI_AGENT_FORCE_DREAM", "") == "1"


# ── Abort support ───────────────────────────────────────────────────

_abort_flag = False


class _DreamAborted(Exception):
    """Sentinel raised when ``abort_consolidation()`` is called."""


def abort_consolidation() -> None:
    """Signal the running consolidation to abort.

    Sets the abort flag which is checked at phase boundaries.
    The consolidation loop will raise ``_DreamAborted``, roll back the
    lock, and clean up partial results.
    """
    global _abort_flag
    _abort_flag = True
    logger.info("Consolidation abort requested")


def _reset_abort_flag() -> None:
    global _abort_flag
    _abort_flag = False


# ── Core consolidation ──────────────────────────────────────────────

async def run_consolidation(
    provider: BaseProvider,
    *,
    memory_dir: str = "",
    project_root: str = "",
    session_dir: str = "",
    current_session: str = "",
    task: DreamTask | None = None,
) -> list[str]:
    """Run memory consolidation. Returns list of files touched."""
    state = _state
    if not memory_dir:
        memory_dir = get_memory_dir(project_root)

    prior_mtime: float | None = None
    if not is_forced():
        prior_mtime = _try_acquire_lock(memory_dir)
        if prior_mtime is None:
            logger.debug("Consolidation skipped — lock held")
            return []

    state.in_progress = True
    start = time.monotonic()

    if task is None:
        task = DreamTask()

    try:
        task.status = DreamTaskStatus.RUNNING
        task.current_phase = "Orient"
        task.progress_pct = 5

        existing = await scan_memory_files(memory_dir)
        manifest = format_memory_manifest(existing)

        sessions: list[SessionInfo] = []
        session_ids: list[str] = []
        if session_dir:
            sessions = _list_sessions_since(
                session_dir, state.last_consolidated_at, current_session,
            )
            session_ids = [s.session_id for s in sessions]

        task.current_phase = "Gather"
        task.total_turns = len(sessions) or 1
        task.progress_pct = 15

        prompt = _build_consolidation_prompt(
            memory_dir,
            manifest,
            len(sessions),
            transcript_dir=session_dir,
            session_ids=session_ids,
        )

        task.current_phase = "Consolidate"
        task.progress_pct = 30

        if _abort_flag:
            raise _DreamAborted

        from ..delegation.subagent import ForkedAgentContext, run_forked_agent

        action_tool = _MemoryActionTool(memory_dir)
        result = await run_forked_agent(
            context=ForkedAgentContext(
                parent_messages=[],
                parent_system_prompt=(
                    "You are a memory consolidation agent. Review and "
                    "improve memory files using the memory_action tool."
                ),
                can_use_tool=lambda tool_name: tool_name == "memory_action",
            ),
            fork_prompt=(
                f"{prompt}\n\n"
                "Use the memory_action tool to create, update, or delete memory files directly. "
                "Do not output a JSON action list. Apply the actions and then stop."
            ),
            provider=provider,
            tools=[action_tool],
            max_turns=6,
            agent_id="auto-dream",
        )

        task.add_turn(result.text)
        task.current_phase = "Prune"
        task.progress_pct = 70

        if _abort_flag:
            raise _DreamAborted

        touched = list(action_tool.touched_paths)

        state.last_consolidated_at = time.time()
        state.consolidation_count += 1
        state.files_touched = touched

        # Stamp the lock with our PID so mtime = now
        lock_path = _get_lock_path(memory_dir)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            f"{os.getpid()}\n{time.time()}", encoding="utf-8",
        )

        elapsed = time.monotonic() - start
        logger.info(
            "Consolidation complete: %d files touched in %.1fs",
            len(touched), elapsed,
        )

        task.complete()
        return touched

    except _DreamAborted:
        logger.info("Consolidation aborted by user")
        task.fail("aborted")
        if prior_mtime is not None:
            _rollback_lock(memory_dir, prior_mtime)
        return []
    except Exception as exc:
        logger.error("Consolidation failed: %s", exc)
        task.fail(str(exc))
        if prior_mtime is not None:
            _rollback_lock(memory_dir, prior_mtime)
        return []
    finally:
        state.in_progress = False
        _reset_abort_flag()

# ── Gate checks ─────────────────────────────────────────────────────

def should_consolidate(
    config: AutoDreamConfig = DEFAULT_CONFIG,
    *,
    memory_dir: str = "",
    session_dir: str = "",
    current_session: str = "",
) -> bool:
    """Check all gates for whether consolidation should run.

    Gate order (cheapest first): enabled → time → scan throttle → sessions.
    ``is_forced()`` bypasses enabled/time/session gates but NOT in-progress.
    """
    state = _state
    forced = is_forced()

    if state.in_progress:
        return False

    if not forced and not config.enabled:
        return False

    if not memory_dir:
        memory_dir = get_memory_dir()

    # Time gate
    last_at = _read_last_consolidated(memory_dir)
    hours_since = (time.time() - last_at) / 3600 if last_at else float("inf")
    if not forced and hours_since < config.min_hours:
        return False

    # Scan throttle
    now = time.monotonic()
    if not forced and now - state.last_scan_at < config.scan_interval_seconds:
        return False
    state.last_scan_at = now

    # Session gate
    if not forced and session_dir:
        sessions = _count_sessions_since(session_dir, last_at, current_session)
        if sessions < config.min_sessions:
            return False

    return True


# ── Hook integration ────────────────────────────────────────────────

class AutoDreamHook(PostSamplingHook):
    """Post-sampling hook that triggers background memory consolidation.

    Checks time + session gates and runs consolidation when both pass.
    """

    def __init__(
        self,
        provider: BaseProvider,
        *,
        memory_dir: str = "",
        session_dir: str = "",
        config: AutoDreamConfig | None = None,
    ) -> None:
        self._provider = provider
        self._memory_dir = memory_dir
        self._session_dir = session_dir
        self._config = config or DEFAULT_CONFIG

    async def on_post_sampling(
        self,
        context: PostSamplingContext,
        *,
        agent: Any = None,
    ) -> None:
        if context.query_source not in ("sdk", "repl_main_thread"):
            return

        current_session = ""
        if agent is not None:
            current_session = str(getattr(agent, "conversation_id", "") or "")

        if not should_consolidate(
            self._config,
            memory_dir=self._memory_dir,
            session_dir=self._session_dir,
            current_session=current_session,
        ):
            return

        asyncio.ensure_future(
            run_consolidation(
                self._provider,
                memory_dir=self._memory_dir,
            )
        )
