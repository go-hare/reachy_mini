"""Inter-agent mailbox — asynchronous message passing between agents.

Mirrors Claude Code's ``teammateMailbox.ts``. Each agent has a named
mailbox (in-memory queue). Leaders and teammates exchange messages,
shutdown requests, and idle notifications through their mailboxes.

**On-disk layout:** Reference uses ``teams/<team>/inboxes/<agent>.json``.
Python's canonical dir is ``teams/<team>/mailboxes/`` (same JSON shape).
:func:`team_files.ensure_reference_inbox_layout` / :func:`team_files.ensure_team_directories`
link ``inboxes`` ↔ ``mailboxes`` when possible so either path resolves to the same files.

Two backends:

- **MemoryMailbox** — in-process list + event per agent (default)
- **FileMailbox** — JSON-based on-disk mailbox (for cross-process teams)
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)


# =====================================================================
# Data structures
# =====================================================================

@dataclass(slots=True)
class MailboxMessage:
    """A single message in an agent's mailbox."""
    from_agent: str
    text: str
    timestamp: str = ""
    color: str | None = None
    summary: str | None = None
    read: bool = False
    msg_type: Literal["message", "idle", "shutdown", "permission"] = "message"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "from": self.from_agent,
            "text": self.text,
            "timestamp": self.timestamp or datetime.now().isoformat(),
            "read": self.read,
            "type": self.msg_type,
        }
        if self.color:
            d["color"] = self.color
        if self.summary:
            d["summary"] = self.summary
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MailboxMessage:
        return cls(
            from_agent=d.get("from", ""),
            text=d.get("text", ""),
            timestamp=d.get("timestamp", ""),
            color=d.get("color"),
            summary=d.get("summary"),
            read=d.get("read", False),
            msg_type=d.get("type", "message"),
        )


@dataclass(slots=True)
class IdleNotification:
    """Notification that an agent has become idle."""
    agent_name: str
    idle_reason: Literal["available", "interrupted", "failed"] = "available"
    summary: str = ""
    completed_task_id: str = ""
    completed_status: Literal["resolved", "blocked", "failed"] | None = None
    failure_reason: str = ""

    def to_text(self) -> str:
        parts = [f'{{"type":"idle","agent":"{self.agent_name}"']
        parts.append(f',"reason":"{self.idle_reason}"')
        if self.summary:
            parts.append(f',"summary":{json.dumps(self.summary)}')
        if self.completed_task_id:
            parts.append(f',"task_id":"{self.completed_task_id}"')
        if self.completed_status:
            parts.append(f',"task_status":"{self.completed_status}"')
        if self.failure_reason:
            parts.append(f',"failure":{json.dumps(self.failure_reason)}')
        parts.append("}")
        return "".join(parts)


def create_idle_notification(
    agent_name: str,
    *,
    idle_reason: str = "available",
    summary: str = "",
    completed_task_id: str = "",
    completed_status: str | None = None,
    failure_reason: str = "",
) -> IdleNotification:
    return IdleNotification(
        agent_name=agent_name,
        idle_reason=idle_reason,  # type: ignore[arg-type]
        summary=summary,
        completed_task_id=completed_task_id,
        completed_status=completed_status,  # type: ignore[arg-type]
        failure_reason=failure_reason,
    )


# =====================================================================
# MemoryMailbox — in-process async queues
# =====================================================================

class MemoryMailbox:
    """In-process mailbox system using asyncio queues.

    Each agent gets a named queue. Messages are delivered immediately
    (non-blocking put) and consumed via ``read`` or ``poll``.

    Usage::

        mailbox = MemoryMailbox()
        mailbox.send("leader", MailboxMessage(from_agent="worker-1", text="done"))
        messages = mailbox.read("leader")
    """

    def __init__(self) -> None:
        self._boxes: dict[str, list[MailboxMessage]] = {}
        self._events: dict[str, asyncio.Event] = {}

    def _ensure(self, name: str) -> None:
        if name not in self._boxes:
            self._boxes[name] = []
            self._events[name] = asyncio.Event()

    def send(self, to: str, message: MailboxMessage) -> None:
        """Deliver a message to an agent's mailbox (non-blocking)."""
        self._ensure(to)
        if not message.timestamp:
            message.timestamp = datetime.now().isoformat()
        self._boxes[to].append(message)
        self._events[to].set()

    def read(self, agent_name: str, *, unread_only: bool = True) -> list[MailboxMessage]:
        """Read all messages from an agent's mailbox."""
        self._ensure(agent_name)
        if unread_only:
            return [m for m in self._boxes[agent_name] if not m.read]
        return list(self._boxes[agent_name])

    def read_and_mark(self, agent_name: str) -> list[MailboxMessage]:
        """Read unread messages and mark them as read."""
        self._ensure(agent_name)
        unread = [m for m in self._boxes[agent_name] if not m.read]
        for m in unread:
            m.read = True
        self._events[agent_name].clear()
        return unread

    def mark_read(self, agent_name: str, index: int) -> None:
        """Mark a specific message as read by index."""
        self._ensure(agent_name)
        msgs = self._boxes[agent_name]
        if 0 <= index < len(msgs):
            msgs[index].read = True

    async def wait_for_message(
        self,
        agent_name: str,
        timeout: float = 0.0,
    ) -> bool:
        """Wait until a new message arrives. Returns True if message received."""
        self._ensure(agent_name)
        unread = [m for m in self._boxes[agent_name] if not m.read]
        if unread:
            return True
        try:
            if timeout > 0:
                await asyncio.wait_for(
                    self._events[agent_name].wait(), timeout=timeout,
                )
            else:
                await self._events[agent_name].wait()
            return True
        except asyncio.TimeoutError:
            return False

    def has_unread(self, agent_name: str) -> bool:
        self._ensure(agent_name)
        return any(not m.read for m in self._boxes[agent_name])

    def clear(self, agent_name: str) -> None:
        """Clear all messages from an agent's mailbox."""
        self._boxes.pop(agent_name, None)
        ev = self._events.pop(agent_name, None)
        if ev:
            ev.clear()

    def clear_all(self) -> None:
        self._boxes.clear()
        for ev in self._events.values():
            ev.clear()
        self._events.clear()

    def agent_names(self) -> list[str]:
        return list(self._boxes.keys())


# =====================================================================
# FileMailbox — cross-process JSON-based mailbox
# =====================================================================

class FileMailbox:
    """File-based mailbox for cross-process agent teams.

    Each agent's mailbox is a JSON file at ``{base_dir}/{agent_name}.json``.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, agent_name: str) -> Path:
        safe = agent_name.replace("/", "_").replace("\\", "_")
        return self._base / f"{safe}.json"

    def _load(self, agent_name: str) -> list[MailboxMessage]:
        p = self._path(agent_name)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return [MailboxMessage.from_dict(d) for d in data]
        except Exception:
            return []

    def _save(self, agent_name: str, messages: list[MailboxMessage]) -> None:
        p = self._path(agent_name)
        p.write_text(
            json.dumps([m.to_dict() for m in messages], indent=2),
            encoding="utf-8",
        )

    def send(self, to: str, message: MailboxMessage) -> None:
        if not message.timestamp:
            message.timestamp = datetime.now().isoformat()
        msgs = self._load(to)
        msgs.append(message)
        self._save(to, msgs)

    def read(self, agent_name: str, *, unread_only: bool = True) -> list[MailboxMessage]:
        msgs = self._load(agent_name)
        if unread_only:
            return [m for m in msgs if not m.read]
        return msgs

    def read_and_mark(self, agent_name: str) -> list[MailboxMessage]:
        msgs = self._load(agent_name)
        unread = [m for m in msgs if not m.read]
        for m in msgs:
            m.read = True
        self._save(agent_name, msgs)
        return unread

    def has_unread(self, agent_name: str) -> bool:
        return any(not m.read for m in self._load(agent_name))

    async def wait_for_message(
        self,
        agent_name: str,
        timeout: float = 0.0,
    ) -> bool:
        import asyncio
        import time

        deadline = time.monotonic() + timeout if timeout > 0 else None
        while True:
            if self.has_unread(agent_name):
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)

    def mark_read(self, agent_name: str, index: int) -> None:
        msgs = self._load(agent_name)
        if 0 <= index < len(msgs):
            msgs[index].read = True
            self._save(agent_name, msgs)

    def clear(self, agent_name: str) -> None:
        p = self._path(agent_name)
        if p.exists():
            p.unlink()

    def clear_all(self) -> None:
        for p in self._base.glob("*.json"):
            p.unlink()

    def agent_names(self) -> list[str]:
        return [p.stem for p in self._base.glob("*.json")]
