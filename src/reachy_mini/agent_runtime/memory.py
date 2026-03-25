"""Minimal session memory for the front-only runtime."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from reachy_mini.agent_runtime.profile_loader import ProfileWorkspace


class MemoryView(BaseModel):
    """Minimal memory structure exposed to the front layer."""

    raw_layer: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    cognitive_layer: list[dict[str, Any]] = Field(default_factory=list)
    long_term_layer: dict[str, Any] = Field(default_factory=dict)
    projections: dict[str, str] = Field(default_factory=dict)


def now_iso() -> str:
    """Return the current timestamp in ISO format."""
    return datetime.now().astimezone().isoformat()


def _append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _sanitize_thread_id(thread_id: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(thread_id or "").strip())
    return normalized or "cli_main"


class FrontSessionStore:
    """Persist recent dialogue in ``session/`` for front-only interactions."""

    def __init__(self, profile: ProfileWorkspace):
        """Store the active profile workspace."""
        self.profile = profile

    def path_for_thread(self, thread_id: str) -> Path:
        """Return the on-disk session path for one thread."""
        return self.profile.session_dir / f"{_sanitize_thread_id(thread_id)}.jsonl"

    def append_dialogue(self, *, thread_id: str, role: str, content: str) -> None:
        """Append one user-visible dialogue turn."""
        _append_jsonl(
            self.path_for_thread(thread_id),
            [
                {
                    "role": str(role or "").strip() or "unknown",
                    "content": str(content or "").strip(),
                    "created_at": now_iso(),
                }
            ],
        )

    def recent_dialogue(self, thread_id: str, limit: int) -> list[dict[str, Any]]:
        """Read recent dialogue rows for a thread."""
        return _read_jsonl(self.path_for_thread(thread_id))[-max(1, limit) :]

    def build_memory_view(self, *, thread_id: str, limit: int) -> MemoryView:
        """Build the front memory view for one thread."""
        return MemoryView(
            raw_layer={"recent_dialogue": self.recent_dialogue(thread_id, limit)},
            projections={
                "agent_anchor": self.profile.agents_md,
                "user_anchor": self.profile.user_md,
                "soul_anchor": self.profile.soul_md,
                "tool_anchor": self.profile.tools_md,
                "front_anchor": self.profile.front_md,
            },
        )
