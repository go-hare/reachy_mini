"""Product-level UX utilities.

Mirrors Claude Code's quality-of-life features:
- Conversation export (markdown, JSON)
- Scheduled tasks (cron-like)
- AutoDream memory consolidation
- Tips system
- Notifications
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .messages import Message, TextBlock, ToolUseBlock, ToolResultBlock


# ── Conversation Export ─────────────────────────────────────────────

def export_markdown(messages: list[Message], *, title: str = "Conversation") -> str:
    """Export conversation as readable Markdown."""
    lines: list[str] = [f"# {title}\n"]

    for msg in messages:
        role = msg.role.capitalize()
        lines.append(f"## {role}\n")

        if isinstance(msg.content, str):
            lines.append(msg.content)
        else:
            for block in msg.content:
                if isinstance(block, TextBlock):
                    lines.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    lines.append(f"**Tool: {block.name}**")
                    lines.append(f"```json\n{json.dumps(block.input, indent=2)}\n```")
                elif isinstance(block, ToolResultBlock):
                    status = "Error" if block.is_error else "Result"
                    preview = block.content[:500]
                    lines.append(f"**{status}:** {preview}")

        lines.append("")

    return "\n".join(lines)


def export_json(messages: list[Message]) -> str:
    """Export conversation as JSON."""
    data: list[dict[str, Any]] = []
    for msg in messages:
        entry: dict[str, Any] = {"role": msg.role}
        if isinstance(msg.content, str):
            entry["content"] = msg.content
        else:
            entry["content"] = []
            for block in msg.content:
                if isinstance(block, TextBlock):
                    entry["content"].append({"type": "text", "text": block.text})
                elif isinstance(block, ToolUseBlock):
                    entry["content"].append({"type": "tool_use", "name": block.name, "input": block.input})
                elif isinstance(block, ToolResultBlock):
                    entry["content"].append({"type": "tool_result", "content": block.content[:500]})
        data.append(entry)
    return json.dumps(data, indent=2, ensure_ascii=False)


def save_export(content: str, path: Path) -> Path:
    """Save exported content to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ── Scheduled Tasks ─────────────────────────────────────────────────

@dataclass
class ScheduledTask:
    """A cron-like scheduled task."""
    task_id: str
    name: str
    prompt: str
    interval_seconds: float
    last_run: float = 0.0
    run_count: int = 0
    enabled: bool = True

    def is_due(self) -> bool:
        if not self.enabled:
            return False
        return (time.time() - self.last_run) >= self.interval_seconds

    def mark_run(self) -> None:
        self.last_run = time.time()
        self.run_count += 1


class TaskScheduler:
    """Manages scheduled tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, ScheduledTask] = {}

    def add(self, task: ScheduledTask) -> None:
        self._tasks[task.task_id] = task

    def remove(self, task_id: str) -> bool:
        return self._tasks.pop(task_id, None) is not None

    def get_due_tasks(self) -> list[ScheduledTask]:
        return [t for t in self._tasks.values() if t.is_due()]

    def list_tasks(self) -> list[ScheduledTask]:
        return list(self._tasks.values())


# ── AutoDream Memory Consolidation ──────────────────────────────────

@dataclass
class DreamResult:
    """Result from an auto-dream consolidation run."""
    insights: list[str] = field(default_factory=list)
    consolidated_count: int = 0
    timestamp: float = field(default_factory=time.time)


async def auto_dream(
    *,
    provider: Any,
    recent_messages: list[Message],
    existing_memories: list[str] | None = None,
) -> DreamResult:
    """Run memory consolidation: distill conversations into insights.

    Uses a side-query to analyze recent interactions and extract
    patterns, preferences, and key facts.
    """
    from .delegation.fork import side_query

    context = "\n".join(existing_memories or [])
    prompt = (
        "Analyze the recent conversation and extract key insights, "
        "user preferences, and important facts. Return as a JSON list of strings."
    )
    if context:
        prompt += f"\n\nExisting memories:\n{context}"

    try:
        result = await side_query(
            provider=provider,
            system_prompt="You are a memory consolidation engine. Extract concise insights.",
            context_messages=recent_messages[-20:],
            prompt=prompt,
            max_tokens=1024,
        )
        insights = _parse_insights(result)
        return DreamResult(insights=insights, consolidated_count=len(insights))
    except Exception:
        return DreamResult()


def _parse_insights(text: str) -> list[str]:
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(item) for item in data]
    except json.JSONDecodeError:
        pass
    return [line.strip("- ").strip() for line in text.strip().splitlines() if line.strip()]


# ── Tips System ─────────────────────────────────────────────────────

TIPS: list[str] = [
    "Use /compact to reduce context size when conversations get long.",
    "Use /cost to check token usage and costs.",
    "Try /buddy to meet your virtual companion!",
    "Use --continue to resume your last session.",
    "Set up ~/.mini-agent/config.json for persistent preferences.",
    "Use -p 'prompt' for one-shot queries without entering interactive mode.",
    "Tools like file_read and grep can help explore codebases.",
    "The bash tool streams output in real-time for long-running commands.",
    "Use /help to see all available slash commands.",
]


def get_random_tip() -> str:
    import random
    return random.choice(TIPS)


def get_tip_of_the_day() -> str:
    day = int(time.time() / 86400)
    return TIPS[day % len(TIPS)]
