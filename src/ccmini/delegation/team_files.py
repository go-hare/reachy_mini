"""Persistent team/swarm file helpers.

This mirrors the recovered runtime's separation between:

- team metadata under ``~/.mini_agent/teams/<team>/`` (as ``team.json``)
- task lists under ``~/.mini_agent/tasks/<team>/``
- a leader-scoped active team pointer (``swarm/leader_team.json``)

**Filename note:** Reference ``swarm/teamHelpers.ts`` stores the roster in
``config.json`` under the team directory; this module uses ``team.json`` for
the same role. Content shape follows the same member fields; do not expect
blind file interchange without renaming.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

from ..paths import mini_agent_path

logger = logging.getLogger(__name__)

TEAM_LEAD_NAME = "team-lead"


def sanitize_name(value: str) -> str:
    cleaned = re.sub(r"[^\w\-.]+", "-", value.strip().lower())
    cleaned = cleaned.strip("-")
    return cleaned or "team"


def get_team_dir(team_name: str) -> Path:
    return mini_agent_path("teams", sanitize_name(team_name))


def get_team_file_path(team_name: str) -> Path:
    return get_team_dir(team_name) / "team.json"


def get_team_mailbox_dir(team_name: str) -> Path:
    """Canonical on-disk mailbox directory (``…/mailboxes``).

    Reference Claude Code uses ``…/inboxes`` for the same JSON files.
    :func:`ensure_reference_inbox_layout` creates ``inboxes`` → ``mailboxes``
    so both layouts see identical files (POSIX symlink; Windows may skip).
    """
    return get_team_dir(team_name) / "mailboxes"


def get_team_inbox_dir(team_name: str) -> Path:
    """Reference layout: ``teams/<team>/inboxes`` (often symlinked to ``mailboxes``)."""
    return get_team_dir(team_name) / "inboxes"


def ensure_reference_inbox_layout(team_name: str) -> None:
    """Align reference ``inboxes/`` with Python ``mailboxes/`` via mutual symlink.

    - If only ``mailboxes`` exists → ``inboxes`` → ``mailboxes``.
    - If only ``inboxes`` exists → ``mailboxes`` → ``inboxes``.
    - If both exist as real directories, leave unchanged (manual merge if needed).
    """
    td = get_team_dir(team_name)
    mb = td / "mailboxes"
    inbox = td / "inboxes"

    mb_exists = mb.exists()
    inbox_exists = inbox.exists()
    if mb_exists and not inbox_exists:
        try:
            inbox.symlink_to(mb.name, target_is_directory=True)
        except OSError:
            logger.debug("inboxes → mailboxes symlink failed", exc_info=True)
        return
    if inbox_exists and not mb_exists:
        try:
            mb.symlink_to(inbox.name, target_is_directory=True)
        except OSError:
            logger.debug("mailboxes → inboxes symlink failed", exc_info=True)
        return

    mb.mkdir(parents=True, exist_ok=True)
    if not inbox_exists:
        try:
            inbox.symlink_to(mb.name, target_is_directory=True)
        except OSError:
            logger.debug("inboxes → mailboxes symlink failed", exc_info=True)


def get_task_dir(team_name: str) -> Path:
    return mini_agent_path("tasks", sanitize_name(team_name))


def get_task_board_path(team_name: str) -> Path:
    return get_task_dir(team_name) / "tasks.json"


def ensure_team_directories(team_name: str) -> None:
    get_team_dir(team_name).mkdir(parents=True, exist_ok=True)
    get_team_mailbox_dir(team_name).mkdir(parents=True, exist_ok=True)
    get_task_dir(team_name).mkdir(parents=True, exist_ok=True)
    ensure_reference_inbox_layout(team_name)


def _leader_team_state_path() -> Path:
    return mini_agent_path("swarm", "leader_team.json")


def set_leader_team_name(team_name: str) -> None:
    path = _leader_team_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"team_name": sanitize_name(team_name), "updated_at": time.time()}, indent=2),
        encoding="utf-8",
    )


def get_leader_team_name() -> str:
    path = _leader_team_state_path()
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    team_name = str(data.get("team_name", "")).strip()
    return sanitize_name(team_name) if team_name else ""


def clear_leader_team_name() -> None:
    path = _leader_team_state_path()
    if path.exists():
        path.unlink()


def read_team_file(team_name: str) -> dict[str, Any] | None:
    path = get_team_file_path(team_name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def write_team_file(team_name: str, data: dict[str, Any]) -> Path:
    path = get_team_file_path(team_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def ensure_team_file(
    team_name: str,
    *,
    description: str = "",
    lead_agent_id: str = "",
    lead_session_id: str = "",
    lead_agent_type: str = TEAM_LEAD_NAME,
    model: str = "",
    cwd: str = "",
) -> dict[str, Any]:
    existing = read_team_file(team_name)
    if existing is not None:
        return existing

    normalized = sanitize_name(team_name)
    data = {
        "name": normalized,
        "description": description,
        "createdAt": int(time.time() * 1000),
        "leadAgentId": lead_agent_id or f"{TEAM_LEAD_NAME}@{normalized}",
        "leadSessionId": lead_session_id,
        "members": [
            {
                "agentId": lead_agent_id or f"{TEAM_LEAD_NAME}@{normalized}",
                "name": TEAM_LEAD_NAME,
                "agentType": lead_agent_type,
                "model": model,
                "joinedAt": int(time.time() * 1000),
                "tmuxPaneId": "",
                "cwd": cwd,
                "subscriptions": [],
                "isActive": True,
                "backendType": "in-process",
            }
        ],
    }
    write_team_file(normalized, data)
    return data


def upsert_team_member(team_name: str, member: dict[str, Any]) -> None:
    data = ensure_team_file(team_name)
    members = data.setdefault("members", [])
    if not isinstance(members, list):
        members = []
        data["members"] = members

    agent_id = str(member.get("agentId", "")).strip()
    updated = False
    for index, existing in enumerate(members):
        if isinstance(existing, dict) and str(existing.get("agentId", "")) == agent_id:
            merged = dict(existing)
            merged.update(member)
            members[index] = merged
            updated = True
            break
    if not updated:
        members.append(member)
    write_team_file(team_name, data)


def set_member_active(team_name: str, agent_id: str, active: bool) -> None:
    data = ensure_team_file(team_name)
    members = data.get("members", [])
    if not isinstance(members, list):
        return
    changed = False
    for member in members:
        if isinstance(member, dict) and str(member.get("agentId", "")) == agent_id:
            if member.get("isActive") != active:
                member["isActive"] = active
                changed = True
            break
    if changed:
        write_team_file(team_name, data)


def remove_team_member(team_name: str, agent_id: str) -> None:
    data = ensure_team_file(team_name)
    members = data.get("members", [])
    if not isinstance(members, list):
        return
    filtered = [
        member
        for member in members
        if not (isinstance(member, dict) and str(member.get("agentId", "")) == agent_id)
    ]
    if len(filtered) != len(members):
        data["members"] = filtered
        write_team_file(team_name, data)


def cleanup_team_directories(team_name: str) -> None:
    for target in (get_team_dir(team_name), get_task_dir(team_name)):
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
