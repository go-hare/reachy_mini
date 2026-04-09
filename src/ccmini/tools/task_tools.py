"""Task CRUD tools."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ..delegation.background import BackgroundAgentRunner
from ..delegation.team_files import get_leader_team_name, sanitize_name
from ..paths import mini_agent_path
from ..tool import Tool, ToolUseContext

TASK_STATUSES = ("pending", "in_progress", "completed")
DEFAULT_TASK_LIST_ID = "tasklist"
HIGH_WATER_MARK_FILE = ".highwatermark"
LOCK_FILE_NAME = ".lock"
LOCK_RETRIES = 30
LOCK_MIN_TIMEOUT = 0.005
LOCK_MAX_TIMEOUT = 0.1


def _sanitize_task_list_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "-", value.strip())


def _lock_file(fp: Any) -> None:
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(fp: Any) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


@dataclass(slots=True)
class TaskRecord:
    """On-disk task row aligned with ``utils/tasks.ts`` ``Task`` / ``TaskSchema``."""

    id: str
    subject: str
    description: str
    activeForm: str = ""
    status: str = "pending"
    owner: str | None = None
    blocks: list[str] = field(default_factory=list)
    blockedBy: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskRecord | None:
        """Parse a JSON object using the same keys as the TS ``Task`` schema."""
        if not isinstance(payload, dict):
            return None
        try:
            return cls(
                id=str(payload["id"]),
                subject=str(payload["subject"]),
                description=str(payload["description"]),
                activeForm=str(payload.get("activeForm", "") or ""),
                owner=(
                    str(payload.get("owner")).strip()
                    if payload.get("owner") is not None and str(payload.get("owner")).strip()
                    else None
                ),
                status=str(payload.get("status", "pending") or "pending"),
                blocks=[str(value) for value in payload.get("blocks", []) if str(value).strip()],
                blockedBy=[str(value) for value in payload.get("blockedBy", []) if str(value).strip()],
                metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
            )
        except Exception:
            return None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not self.activeForm:
            payload.pop("activeForm", None)
        if self.owner is None:
            payload.pop("owner", None)
        if not self.metadata:
            payload.pop("metadata", None)
        return payload


class TaskBoard:
    """File-backed task list mirroring Claude Code's ``utils/tasks.ts``."""

    def __init__(self, path: Path | None = None) -> None:
        self._path_override = path
        self._scope_override = ""

    def set_scope(self, task_list_id: str = "") -> None:
        self._scope_override = _sanitize_task_list_id(task_list_id) if task_list_id else ""

    def _resolve_dir(self) -> Path:
        if self._path_override is not None:
            if self._path_override.suffix == ".json":
                return self._path_override.parent
            return self._path_override
        task_list_id = self._scope_override or _sanitize_task_list_id(get_leader_team_name()) or DEFAULT_TASK_LIST_ID
        return mini_agent_path("tasks", task_list_id)

    def _ensure_tasks_dir(self) -> Path:
        path = self._resolve_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _lock_path(self) -> Path:
        return self._ensure_tasks_dir() / LOCK_FILE_NAME

    def _high_water_mark_path(self) -> Path:
        return self._ensure_tasks_dir() / HIGH_WATER_MARK_FILE

    def _task_path(self, task_id: str) -> Path:
        safe_task_id = _sanitize_task_list_id(task_id)
        return self._ensure_tasks_dir() / f"{safe_task_id}.json"

    @contextmanager
    def _locked(self, path: Path) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as fp:
            for attempt in range(LOCK_RETRIES):
                try:
                    _lock_file(fp)
                    break
                except Exception:
                    if attempt == LOCK_RETRIES - 1:
                        raise
                    time.sleep(min(LOCK_MIN_TIMEOUT * (2**attempt), LOCK_MAX_TIMEOUT))
            try:
                yield
            finally:
                _unlock_file(fp)

    def _read_high_water_mark(self) -> int:
        path = self._high_water_mark_path()
        try:
            return int(path.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            return 0

    def _write_high_water_mark(self, value: int) -> None:
        self._high_water_mark_path().write_text(str(value), encoding="utf-8")

    def _parse_record(self, payload: Any) -> TaskRecord | None:
        return TaskRecord.from_dict(payload) if isinstance(payload, dict) else None

    def _read_task(self, task_id: str) -> TaskRecord | None:
        path = self._task_path(str(task_id))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return self._parse_record(payload)

    def _write_task(self, record: TaskRecord) -> None:
        self._task_path(record.id).write_text(
            json.dumps(record.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _find_highest_task_id_from_files(self) -> int:
        highest = 0
        for path in self._ensure_tasks_dir().glob("*.json"):
            try:
                task_id = int(path.stem)
            except ValueError:
                continue
            highest = max(highest, task_id)
        return highest

    def _find_highest_task_id(self) -> int:
        return max(self._find_highest_task_id_from_files(), self._read_high_water_mark())

    def create(
        self,
        *,
        subject: str,
        description: str,
        active_form: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TaskRecord:
        with self._locked(self._lock_path()):
            task_id = str(self._find_highest_task_id() + 1)
            record = TaskRecord(
                id=task_id,
                subject=subject,
                description=description,
                activeForm=active_form,
                status="pending",
                owner=None,
                blocks=[],
                blockedBy=[],
                metadata=dict(metadata or {}),
            )
            self._write_task(record)
            return record

    def get(self, task_id: str) -> TaskRecord | None:
        return self._read_task(str(task_id))

    def list(self) -> list[TaskRecord]:
        records: list[TaskRecord] = []
        for path in sorted(
            self._ensure_tasks_dir().glob("*.json"),
            key=lambda value: (0, int(value.stem)) if value.stem.isdigit() else (1, value.stem),
        ):
            record = self._read_task(path.stem)
            if record is not None:
                records.append(record)
        return records

    def delete(self, task_id: str) -> bool:
        normalized_task_id = str(task_id)
        with self._locked(self._lock_path()):
            record = self.get(normalized_task_id)
            if record is None:
                return False

            try:
                numeric_id = int(normalized_task_id)
            except ValueError:
                numeric_id = -1
            if numeric_id >= 0:
                current_mark = self._read_high_water_mark()
                if numeric_id > current_mark:
                    self._write_high_water_mark(numeric_id)

            try:
                self._task_path(normalized_task_id).unlink()
            except FileNotFoundError:
                return False

            for task in self.list():
                new_blocks = [value for value in task.blocks if value != normalized_task_id]
                new_blocked_by = [value for value in task.blockedBy if value != normalized_task_id]
                if new_blocks != task.blocks or new_blocked_by != task.blockedBy:
                    task.blocks = new_blocks
                    task.blockedBy = new_blocked_by
                    self._write_task(task)
            return True

    def reset(self) -> bool:
        """Clear the current task list while preserving the high-water mark."""
        with self._locked(self._lock_path()):
            current_highest = self._find_highest_task_id_from_files()
            if current_highest > 0:
                existing_mark = self._read_high_water_mark()
                if current_highest > existing_mark:
                    self._write_high_water_mark(current_highest)

            removed_any = False
            for path in self._ensure_tasks_dir().glob("*.json"):
                try:
                    path.unlink()
                    removed_any = True
                except FileNotFoundError:
                    continue
            return removed_any

    def block(self, from_task_id: str, to_task_id: str) -> bool:
        with self._locked(self._lock_path()):
            source = self.get(str(from_task_id))
            target = self.get(str(to_task_id))
            if source is None or target is None:
                return False

            changed = False
            if target.id not in source.blocks:
                source.blocks.append(target.id)
                self._write_task(source)
                changed = True
            if source.id not in target.blockedBy:
                target.blockedBy.append(source.id)
                self._write_task(target)
                changed = True
            return changed or True

    def update(self, task_id: str, **changes: Any) -> tuple[TaskRecord | None, list[str], dict[str, str] | None]:
        normalized_task_id = str(task_id)
        existing = self.get(normalized_task_id)
        if existing is None:
            return None, [], None

        status = changes.get("status")
        if status == "deleted":
            deleted = self.delete(normalized_task_id)
            return (existing if deleted else None), (["deleted"] if deleted else []), (
                {"from": existing.status, "to": "deleted"} if deleted else None
            )

        # Use the shared lock file so Windows can still reopen the task file
        # while the update lock is held.
        with self._locked(self._lock_path()):
            record = self.get(normalized_task_id)
            if record is None:
                return None, [], None

            updated_fields: list[str] = []
            status_change: dict[str, str] | None = None

            def _set(attr: str, value: Any) -> None:
                nonlocal status_change
                if value is None:
                    return
                current = getattr(record, attr)
                if current == value:
                    return
                setattr(record, attr, value)
                updated_fields.append(attr)
                if attr == "status":
                    status_change = {"from": str(current), "to": str(value)}

            _set("subject", changes.get("subject"))
            _set("description", changes.get("description"))
            _set("activeForm", changes.get("activeForm"))
            _set("owner", changes.get("owner"))
            if status is not None:
                _set("status", status)

            metadata = changes.get("metadata")
            if metadata is not None and isinstance(metadata, dict):
                merged = dict(record.metadata)
                for key, value in metadata.items():
                    if value is None:
                        merged.pop(key, None)
                    else:
                        merged[key] = value
                if merged != record.metadata:
                    record.metadata = merged
                    updated_fields.append("metadata")

            if updated_fields:
                self._write_task(record)
            return record, updated_fields, status_change


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _task_list_id_from_context(context: ToolUseContext) -> str:
    explicit_task_list_id = os.environ.get("CLAUDE_CODE_TASK_LIST_ID", "").strip()
    if explicit_task_list_id:
        return _sanitize_task_list_id(explicit_task_list_id)

    agent_id = str(getattr(context, "agent_id", "") or "").strip()
    if "@" in agent_id:
        return sanitize_name(agent_id.split("@", 1)[1].strip())

    extras = getattr(context, "extras", {}) or {}
    for key in ("task_list_id", "team_name", "active_team_name"):
        value = str(extras.get(key, "") or "").strip()
        if value:
            return _sanitize_task_list_id(value)

    team_name = os.environ.get("CLAUDE_CODE_TEAM_NAME", "").strip()
    if team_name:
        return _sanitize_task_list_id(team_name)

    leader_team_name = get_leader_team_name()
    if leader_team_name:
        return _sanitize_task_list_id(leader_team_name)

    conversation_id = str(getattr(context, "conversation_id", "") or "").strip()
    if conversation_id:
        return _sanitize_task_list_id(conversation_id)

    return DEFAULT_TASK_LIST_ID


def _sync_team_task_list(
    context: ToolUseContext,
    *,
    record: TaskRecord | None = None,
    removed_task_id: str = "",
) -> None:
    extras = getattr(context, "extras", {}) or {}
    agent = extras.get("agent")
    team_tool = getattr(agent, "_team_create_tool", None) if agent is not None else None
    team_name = (
        str(extras.get("team_name", "") or "").strip()
        or str(extras.get("active_team_name", "") or "").strip()
        or str(getattr(team_tool, "_active_team_name", "") or "").strip()
    )
    if not team_name or team_tool is None:
        return

    team = team_tool.get_team(team_name)
    if team is None:
        return

    task_list = getattr(team, "task_list", None)
    if task_list is None:
        return

    if removed_task_id:
        task_list.remove(removed_task_id)
        return

    if record is None:
        return

    task_list.upsert(
        task_id=record.id,
        subject=record.subject,
        description=record.description,
        status=record.status,
        owner=record.owner or "",
        blocked_by=list(record.blockedBy),
    )


class TaskCreateTool(Tool):
    name = "TaskCreate"
    description = "Create a new task in the task list."
    is_read_only = False

    def __init__(self, board: TaskBoard) -> None:
        self._board = board

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "A brief title for the task."},
                "description": {"type": "string", "description": "What needs to be done."},
                "activeForm": {"type": "string", "description": "Present continuous form shown in spinner when in_progress."},
                "metadata": {"type": "object", "description": "Arbitrary metadata to attach to the task."},
            },
            "required": ["subject", "description"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        self._board.set_scope(_task_list_id_from_context(context))
        task = self._board.create(
            subject=str(kwargs["subject"]),
            description=str(kwargs["description"]),
            active_form=str(kwargs.get("activeForm", "")),
            metadata=kwargs.get("metadata") or {},
        )
        _sync_team_task_list(context, record=task)
        return _json({"task": {"id": task.id, "subject": task.subject}})


class TaskGetTool(Tool):
    name = "TaskGet"
    description = "Get a task by ID from the task list."
    is_read_only = True

    def __init__(self, board: TaskBoard) -> None:
        self._board = board

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"taskId": {"type": "string", "description": "The ID of the task to retrieve."}},
            "required": ["taskId"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        self._board.set_scope(_task_list_id_from_context(context))
        task = self._board.get(str(kwargs["taskId"]))
        if task is None:
            return _json({"task": None})
        return _json(
            {
                "task": {
                    "id": task.id,
                    "subject": task.subject,
                    "description": task.description,
                    "status": task.status,
                    "blocks": task.blocks,
                    "blockedBy": task.blockedBy,
                }
            }
        )


class TaskListTool(Tool):
    name = "TaskList"
    description = "List all tasks in the task list."
    is_read_only = True

    def __init__(self, board: TaskBoard) -> None:
        self._board = board

    def get_parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        self._board.set_scope(_task_list_id_from_context(context))
        all_tasks = [task for task in self._board.list() if not task.metadata.get("_internal")]
        resolved_ids = {task.id for task in all_tasks if task.status == "completed"}
        tasks = [
            {
                "id": task.id,
                "subject": task.subject,
                "status": task.status,
                "owner": task.owner,
                "blockedBy": [value for value in task.blockedBy if value not in resolved_ids],
            }
            for task in all_tasks
        ]
        return _json({"tasks": tasks})


class TaskUpdateTool(Tool):
    name = "TaskUpdate"
    description = "Update a task in the task list."
    is_read_only = False

    def __init__(self, board: TaskBoard) -> None:
        self._board = board

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "taskId": {"type": "string", "description": "The ID of the task to update."},
                "subject": {"type": "string", "description": "New subject for the task."},
                "description": {"type": "string", "description": "New description for the task."},
                "activeForm": {"type": "string", "description": "Present continuous form shown in spinner when in_progress."},
                "status": {"type": "string", "enum": [*TASK_STATUSES, "deleted"], "description": "New status for the task."},
                "addBlocks": {"type": "array", "items": {"type": "string"}, "description": "Task IDs that this task blocks."},
                "addBlockedBy": {"type": "array", "items": {"type": "string"}, "description": "Task IDs that block this task."},
                "owner": {"type": "string", "description": "New owner for the task."},
                "metadata": {"type": "object", "description": "Metadata keys to merge. Set a key to null to delete it."},
            },
            "required": ["taskId"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        self._board.set_scope(_task_list_id_from_context(context))
        task_id = str(kwargs["taskId"])
        existing = self._board.get(task_id)
        if existing is None:
            return _json({"success": False, "taskId": task_id, "updatedFields": [], "error": "Task not found"})

        owner = kwargs.get("owner")
        status = kwargs.get("status")
        if (
            status == "in_progress"
            and owner is None
            and not existing.owner
        ):
            inferred_owner = str(getattr(context, "agent_id", "") or "").strip()
            if inferred_owner:
                owner = inferred_owner

        _record, updated_fields, status_change = self._board.update(
            task_id,
            subject=kwargs.get("subject"),
            description=kwargs.get("description"),
            activeForm=kwargs.get("activeForm"),
            status=status,
            owner=owner,
            metadata=kwargs.get("metadata"),
        )

        add_blocks = [str(v) for v in (kwargs.get("addBlocks") or []) if str(v).strip()]
        for blocked_task_id in add_blocks:
            if self._board.block(task_id, blocked_task_id) and "blocks" not in updated_fields:
                updated_fields.append("blocks")

        add_blocked_by = [str(v) for v in (kwargs.get("addBlockedBy") or []) if str(v).strip()]
        for blocker_task_id in add_blocked_by:
            if self._board.block(blocker_task_id, task_id) and "blockedBy" not in updated_fields:
                updated_fields.append("blockedBy")

        updated_record = self._board.get(task_id)
        if "deleted" in updated_fields:
            _sync_team_task_list(context, removed_task_id=task_id)
        elif updated_record is not None:
            _sync_team_task_list(context, record=updated_record)

        payload: dict[str, Any] = {
            "success": True,
            "taskId": task_id,
            "updatedFields": updated_fields,
        }
        if status_change is not None:
            payload["statusChange"] = status_change
        return _json(payload)


class TaskOutputTool(Tool):
    name = "TaskOutput"
    description = "[Deprecated] — prefer Read on the task output file path"
    is_read_only = True

    def __init__(self, runner: BackgroundAgentRunner) -> None:
        self._runner = runner

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task ID to get output from."},
                "agentId": {"type": "string", "description": "Compatibility alias for task_id."},
                "block": {"type": "boolean", "description": "Whether to wait for completion.", "default": True},
                "timeout": {"type": "integer", "description": "Max wait time in ms.", "default": 30000},
            },
            "required": [],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        del context
        task_id = str(kwargs.get("task_id") or kwargs.get("agentId") or "").strip()
        if not task_id:
            return _json({"retrieval_status": "not_found", "task": None, "error": "Missing required parameter: task_id"})
        block = bool(kwargs.get("block", True))
        timeout_ms = int(kwargs.get("timeout", 30000) or 30000)

        status = self._runner.get_status(task_id)
        if status is None:
            return _json({"retrieval_status": "not_found", "task": None, "error": f"No task found with ID: {task_id}"})

        def _status_value(task_info: Any) -> str:
            raw = getattr(task_info, "status", "")
            return raw.value if hasattr(raw, "value") else str(raw)

        if block and _status_value(status) in {"pending", "running"}:
            waited = 0
            while waited < timeout_ms:
                await asyncio.sleep(0.1)
                waited += 100
                status = self._runner.get_status(task_id)
                if status is None or _status_value(status) not in {"pending", "running"}:
                    break

        status = self._runner.get_status(task_id)
        if status is None:
            return _json({"retrieval_status": "not_found", "task": None, "error": f"No task found with ID: {task_id}"})

        retrieval_status = "success"
        status_value = _status_value(status)
        if status_value in {"pending", "running"}:
            retrieval_status = "timeout" if block else "not_ready"

        task_payload = {
            "task_id": status.id,
            "task_type": "local_agent",
            "status": status_value,
            "description": status.description,
            "output": "",
            "error": status.error or None,
        }
        output_file = str(getattr(status, "output_file", "") or "").strip()
        if output_file:
            try:
                task_payload["output"] = Path(output_file).read_text(encoding="utf-8")
            except Exception:
                task_payload["output"] = status.result or ""
            task_payload["output_file"] = output_file
        else:
            task_payload["output"] = status.result or ""
        return _json({"retrieval_status": retrieval_status, "task": task_payload})
