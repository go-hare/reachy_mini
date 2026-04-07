"""ListPeersTool — discover sibling agent sessions.

Ported from Claude Code's ``ListPeersTool`` (UDS inbox system):
- Discovers other running mini-agent sessions on the local machine
- Uses PID-file-based session registry (cross-platform, no UDS dependency)
- Shows each peer's session ID, name, status, and messaging address
- The model uses this to find targets for ``SendMessage``

Architecture:
- Each agent session registers itself in ``~/.ccmini/sessions/``
- A PID file contains JSON metadata (pid, name, status, socket path)
- ListPeersTool scans the registry, probes PIDs, and returns live peers
- Stale entries (dead PIDs) are automatically cleaned up

This enables multi-session workflows where agents collaborate:
    session A: "What sessions are running?"  → ListPeers
    session A: "Ask session B to review auth" → SendMessage(to=B)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..delegation.mailbox import FileMailbox, MailboxMessage
from ..paths import mini_agent_path
from ..tool import Tool, ToolUseContext

logger = logging.getLogger(__name__)


# ── Session registry ────────────────────────────────────────────────

def _get_sessions_dir() -> Path:
    """Get the session registry directory."""
    return mini_agent_path("sessions")


def _get_session_mailbox_dir() -> Path:
    return mini_agent_path("session_mailboxes")


@dataclass(slots=True)
class PeerInfo:
    """Information about a discovered peer session."""
    session_id: str
    pid: int
    name: str = ""
    status: str = "unknown"
    working_dir: str = ""
    started_at: float = 0.0
    model: str = ""
    agent_id: str = ""

    @property
    def is_alive(self) -> bool:
        """Check if the peer process is still running."""
        return _is_pid_alive(self.pid)

    @property
    def uptime_str(self) -> str:
        if self.started_at <= 0:
            return "unknown"
        elapsed = time.time() - self.started_at
        if elapsed < 60:
            return f"{int(elapsed)}s"
        if elapsed < 3600:
            return f"{int(elapsed / 60)}m"
        return f"{elapsed / 3600:.1f}h"


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID exists."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, PermissionError):
        return False


# ── Registration ────────────────────────────────────────────────────

def register_session(
    session_id: str,
    *,
    name: str = "",
    working_dir: str = "",
    model: str = "",
    agent_id: str = "",
) -> Path:
    """Register the current session in the peer registry.

    Creates a PID file with session metadata. Call at agent startup.
    Returns the path to the PID file (for cleanup on exit).
    """
    sessions_dir = _get_sessions_dir()
    sessions_dir.mkdir(parents=True, exist_ok=True)

    pid_file = sessions_dir / f"{session_id}.json"
    data = {
        "session_id": session_id,
        "pid": os.getpid(),
        "name": name,
        "status": "running",
        "working_dir": working_dir or os.getcwd(),
        "started_at": time.time(),
        "model": model,
        "agent_id": agent_id,
    }

    pid_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.debug("Registered session %s (pid=%d)", session_id, os.getpid())
    return pid_file


def update_session_status(session_id: str, status: str) -> None:
    """Update the status field of a registered session."""
    pid_file = _get_sessions_dir() / f"{session_id}.json"
    if not pid_file.exists():
        return
    try:
        data = json.loads(pid_file.read_text(encoding="utf-8"))
        data["status"] = status
        pid_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (json.JSONDecodeError, OSError):
        pass


def update_session_name(session_id: str, name: str) -> None:
    """Update the name field of a registered session."""
    pid_file = _get_sessions_dir() / f"{session_id}.json"
    if not pid_file.exists():
        return
    try:
        data = json.loads(pid_file.read_text(encoding="utf-8"))
        data["name"] = name
        pid_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (json.JSONDecodeError, OSError):
        pass


def unregister_session(session_id: str) -> None:
    """Remove a session from the registry. Call at agent shutdown."""
    pid_file = _get_sessions_dir() / f"{session_id}.json"
    try:
        pid_file.unlink(missing_ok=True)
        logger.debug("Unregistered session %s", session_id)
    except OSError:
        pass
    try:
        FileMailbox(_get_session_mailbox_dir()).clear(session_id)
    except Exception:
        pass


def send_session_message(
    session_id: str,
    *,
    from_name: str,
    text: str,
    summary: str | None = None,
) -> None:
    mailbox = FileMailbox(_get_session_mailbox_dir())
    mailbox.send(
        session_id,
        MailboxMessage(
            from_agent=from_name,
            text=text,
            summary=summary,
        ),
    )


def read_session_messages(session_id: str) -> list[MailboxMessage]:
    mailbox = FileMailbox(_get_session_mailbox_dir())
    return mailbox.read_and_mark(session_id)


# ── Discovery ───────────────────────────────────────────────────────

def list_live_peers(
    *,
    exclude_session: str = "",
    cleanup_stale: bool = True,
) -> list[PeerInfo]:
    """Scan the session registry and return live peers.

    Probes each PID file, filters dead processes, and optionally
    cleans up stale entries.
    """
    sessions_dir = _get_sessions_dir()
    if not sessions_dir.is_dir():
        return []

    peers: list[PeerInfo] = []
    stale: list[Path] = []

    for pid_file in sessions_dir.glob("*.json"):
        try:
            data = json.loads(pid_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            stale.append(pid_file)
            continue

        session_id = data.get("session_id", pid_file.stem)
        if session_id == exclude_session:
            continue

        pid = data.get("pid", 0)
        peer = PeerInfo(
            session_id=session_id,
            pid=pid,
            name=data.get("name", ""),
            status=data.get("status", "unknown"),
            working_dir=data.get("working_dir", ""),
            started_at=data.get("started_at", 0.0),
            model=data.get("model", ""),
            agent_id=data.get("agent_id", ""),
        )

        if peer.is_alive:
            peers.append(peer)
        else:
            stale.append(pid_file)

    # Clean up stale entries
    if cleanup_stale:
        for path in stale:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if stale:
            logger.debug("Cleaned up %d stale session entries", len(stale))

    return peers


def count_live_sessions() -> int:
    """Count currently running sessions (including self)."""
    return len(list_live_peers(cleanup_stale=False))


# ── ListPeersTool ───────────────────────────────────────────────────

class ListPeersTool(Tool):
    """Discover other running agent sessions.

    Scans the session registry to find live sibling sessions.
    Use this to find targets for cross-session communication
    via SendMessage.
    """

    name = "ListPeers"
    description = (
        "List other running agent sessions on this machine. "
        "Shows session ID, name, status, working directory, and uptime. "
        "Use the session ID with SendMessage for cross-session communication."
    )
    instructions = """\
Discover sibling agent sessions that are running on the same machine.

Each peer has:
- **session_id** — unique identifier, use as target for messaging
- **name** — human-readable session name (if set)
- **status** — running, idle, or other state
- **working_dir** — the directory the peer is working in
- **uptime** — how long the session has been running
- **model** — the model the peer is using

Use cases:
- Find running agents to delegate work to via SendMessage
- Check if a peer is working on a related task
- Coordinate across multiple sessions working on the same project"""
    is_read_only = True

    def __init__(self, current_session_id: str = "") -> None:
        self._current_session = current_session_id

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "include_self": {
                    "type": "boolean",
                    "description": "Include the current session in results (default: false)",
                    "default": False,
                },
            },
        }

    async def execute(
        self,
        *,
        context: ToolUseContext,
        include_self: bool = False,
        **kwargs: Any,
    ) -> str:
        exclude = "" if include_self else (
            self._current_session or context.extras.get("session_id", "")
        )

        peers = list_live_peers(exclude_session=exclude)

        if not peers:
            return "No other agent sessions are currently running."

        lines: list[str] = [f"Found {len(peers)} peer session(s):\n"]
        for peer in sorted(peers, key=lambda p: p.started_at, reverse=True):
            name_part = f" ({peer.name})" if peer.name else ""
            model_part = f", model: {peer.model}" if peer.model else ""
            lines.append(
                f"- **{peer.session_id}**{name_part}\n"
                f"  status: {peer.status}, uptime: {peer.uptime_str}{model_part}\n"
                f"  working_dir: {peer.working_dir}"
            )

        return "\n".join(lines)
