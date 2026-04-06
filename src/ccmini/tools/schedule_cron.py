"""Cron task tools backed by the Kairos persistent scheduler store."""

from __future__ import annotations

import json
from typing import Any

from ..kairos import TaskStore, create_cron_task, delete_cron_task, list_cron_tasks
from ..tool import Tool, ToolUseContext


class CronCreateTool(Tool):
    name = "CronCreate"
    description = "Create a durable recurring cron task for Kairos."
    is_read_only = False

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Cron task name."},
                "cron_expr": {"type": "string", "description": "Five-field cron expression."},
                "prompt": {"type": "string", "description": "Prompt to run when the cron fires."},
                "enabled": {"type": "boolean", "description": "Whether the cron starts enabled.", "default": True},
            },
            "required": ["name", "cron_expr", "prompt"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        task = create_cron_task(
            self._store,
            name=kwargs["name"],
            cron_expr=kwargs["cron_expr"],
            prompt=kwargs["prompt"],
        )
        task.enabled = bool(kwargs.get("enabled", True))
        self._store.add(task)
        return json.dumps(task.to_dict(), indent=2, ensure_ascii=False)


class CronListTool(Tool):
    name = "CronList"
    description = "List durable Kairos cron tasks."
    is_read_only = True

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    def get_parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        tasks = [task.to_dict() for task in list_cron_tasks(self._store)]
        return json.dumps({"tasks": tasks}, indent=2, ensure_ascii=False)


class CronDeleteTool(Tool):
    name = "CronDelete"
    description = "Delete a durable Kairos cron task."
    is_read_only = False

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Cron task ID to delete."},
            },
            "required": ["task_id"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        task_id = kwargs["task_id"]
        deleted = delete_cron_task(self._store, task_id)
        if not deleted:
            return f"Cron task not found: {task_id}"
        return json.dumps({"deleted": task_id}, indent=2, ensure_ascii=False)
