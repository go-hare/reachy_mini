"""TodoWriteTool — structured task list management for the LLM.

Mirrors Claude Code's TodoWriteTool: the LLM creates, updates, and
tracks progress on a todo list.  The host UI can render this list
to show the user real-time progress.

The tool stores state as a simple in-memory dict keyed by ``id``.
Callers can read the current state via ``TodoWriteTool.todos``.

Extended with:
- Deep merge (partial updates, preserve unmodified fields)
- Persistence to ~/.mini_agent/todos.json
- Todo dependencies (depends_on, blockers)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..paths import mini_agent_home
from ..tool import Tool, ToolUseContext

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "Update the todo list for the current session. To be used proactively "
    "and often to track progress and pending tasks. Make sure that at least "
    "one task is in_progress at all times."
)

INSTRUCTIONS = """\
Use this tool to create and manage a structured task list for your current \
coding session. This helps you track progress, organize complex tasks, and \
demonstrate thoroughness to the user.

## When to Use This Tool

Use this tool proactively in these scenarios:
1. Complex multi-step tasks (3+ distinct steps)
2. Non-trivial tasks requiring careful planning
3. User explicitly requests a todo list
4. User provides multiple tasks (numbered or comma-separated)
5. After receiving new instructions — capture requirements as todos
6. When starting a task — mark it as in_progress BEFORE beginning work
7. After completing a task — mark it as completed immediately

## When NOT to Use

Skip when:
1. Single, straightforward task
2. Trivial task with no organizational benefit
3. Completable in < 3 trivial steps
4. Purely conversational or informational request

## Task States

- **pending**: Not yet started
- **in_progress**: Currently working on (limit ONE at a time)
- **completed**: Finished successfully

## Task Management Rules

- Update status in real-time as you work
- Mark tasks complete IMMEDIATELY after finishing (don't batch)
- Exactly ONE task should be in_progress at any time
- Complete current tasks before starting new ones
- ONLY mark completed when FULLY accomplished
- If blocked, keep as in_progress and create a new task for the blocker
- Use depends_on to declare that a task must wait on another task

## Task Breakdown

- Create specific, actionable items
- Break complex tasks into smaller, manageable steps
- Use clear, descriptive task names

When in doubt, use this tool. Being proactive with task management \
demonstrates attentiveness and ensures all requirements are completed.\
"""

_PERSIST_DIR = mini_agent_home()
_PERSIST_FILE = _PERSIST_DIR / "todos.json"


class TodoWriteTool(Tool):
    """Structured todo list for LLM task tracking."""

    name = "TodoWrite"
    description = DESCRIPTION
    instructions = INSTRUCTIONS
    is_read_only = False

    def __init__(self, *, persist: bool = True) -> None:
        self._todos: dict[str, dict[str, Any]] = {}
        self._persist = persist
        if persist:
            self._load_todos()

    @property
    def todos(self) -> dict[str, dict[str, Any]]:
        """Current todo state — ``{id: {content, status, ...}}``."""
        return dict(self._todos)

    @property
    def todo_list(self) -> list[dict[str, Any]]:
        """Ordered list of todos for rendering."""
        return list(self._todos.values())

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "Array of TODO items to create or update.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Unique identifier for the TODO item.",
                            },
                            "content": {
                                "type": "string",
                                "description": "Description of the task (imperative form).",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "Current status of the TODO item.",
                            },
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "IDs of tasks this todo depends on. "
                                    "Cannot be completed until all dependencies are done."
                                ),
                            },
                        },
                        "required": ["id"],
                    },
                    "minItems": 1,
                },
                "merge": {
                    "type": "boolean",
                    "description": (
                        "When true (default), merge updates into existing "
                        "todos preserving unmodified fields. When false, "
                        "replace the entire todo list."
                    ),
                },
            },
            "required": ["todos"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        todos = kwargs.get("todos", [])
        merge = kwargs.get("merge", True)

        if not todos:
            return "Error: 'todos' array is required and must not be empty."

        if not merge:
            self._todos.clear()

        created = 0
        updated = 0

        for item in todos:
            todo_id = item.get("id", "")
            if not todo_id:
                continue

            if todo_id in self._todos:
                _deep_merge_todo(self._todos[todo_id], item)
                updated += 1
            else:
                self._todos[todo_id] = dict(item)
                created += 1

        # Block completion of todos whose dependencies aren't done
        blocked_ids: list[str] = []
        for tid, t in self._todos.items():
            if t.get("status") == "completed":
                blockers = self.get_blockers(tid)
                if blockers:
                    t["status"] = "in_progress"
                    blocked_ids.append(tid)

        if self._persist:
            self._save_todos()

        summary_parts = []
        if created:
            summary_parts.append(f"{created} created")
        if updated:
            summary_parts.append(f"{updated} updated")
        if blocked_ids:
            summary_parts.append(
                f"{len(blocked_ids)} blocked by dependencies"
            )

        status_counts = {"pending": 0, "in_progress": 0, "completed": 0}
        for t in self._todos.values():
            s = t.get("status", "pending")
            if s in status_counts:
                status_counts[s] += 1

        return (
            f"Todo list updated: {', '.join(summary_parts) or 'no changes'}. "
            f"Total: {len(self._todos)} "
            f"(pending={status_counts['pending']}, "
            f"in_progress={status_counts['in_progress']}, "
            f"completed={status_counts['completed']})"
        )

    # ── Dependencies ─────────────────────────────────────────────────

    def get_blockers(self, todo_id: str) -> list[str]:
        """Return IDs of incomplete tasks that *todo_id* depends on."""
        todo = self._todos.get(todo_id)
        if todo is None:
            return []
        deps = todo.get("depends_on", [])
        if not deps:
            return []
        blockers: list[str] = []
        for dep_id in deps:
            dep = self._todos.get(dep_id)
            if dep is None or dep.get("status") != "completed":
                blockers.append(dep_id)
        return blockers

    # ── Persistence ──────────────────────────────────────────────────

    def _save_todos(self) -> None:
        try:
            _PERSIST_DIR.mkdir(parents=True, exist_ok=True)
            _PERSIST_FILE.write_text(
                json.dumps(list(self._todos.values()), indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Failed to persist todos: %s", exc)

    def _load_todos(self) -> None:
        if not _PERSIST_FILE.is_file():
            return
        try:
            data = json.loads(_PERSIST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    tid = item.get("id")
                    if tid:
                        self._todos[tid] = item
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load persisted todos: %s", exc)


# ── Deep merge helper ────────────────────────────────────────────────


def _deep_merge_todo(existing: dict[str, Any], update: dict[str, Any]) -> None:
    """Merge *update* into *existing*, preserving unmodified fields.

    Only keys present in *update* are overwritten. This allows partial
    updates such as changing only ``status`` without resupplying
    ``content``.
    """
    for key, value in update.items():
        if key == "id":
            continue
        if (
            isinstance(value, dict)
            and isinstance(existing.get(key), dict)
        ):
            _deep_merge_todo(existing[key], value)
        else:
            existing[key] = value
