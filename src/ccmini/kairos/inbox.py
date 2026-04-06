"""Persistent inbox for Kairos file deliveries and push-style notifications.

Writes JSON lines under ``~/.mini_agent/kairos_inbox/`` so hosts (CLI, HTTP,
bridge) can tail or poll without coupling to the agent process.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from ..paths import mini_agent_path

_lock = threading.Lock()
_DIR = mini_agent_path("kairos_inbox")


def _append(kind: str, record: dict[str, Any]) -> Path:
    _DIR.mkdir(parents=True, exist_ok=True)
    path = _DIR / f"{kind}.jsonl"
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with _lock:
        path.open("a", encoding="utf-8").write(line)
    return path


def record_file_delivery(
    *,
    conversation_id: str,
    source_path: str,
    byte_length: int,
    content_sha256: str,
    caption: str = "",
) -> Path:
    """Append a SendUserFile delivery record."""
    return _append(
        "file_deliveries",
        {
            "type": "send_user_file",
            "ts": time.time(),
            "conversation_id": conversation_id,
            "source_path": source_path,
            "byte_length": byte_length,
            "content_sha256": content_sha256,
            "caption": caption,
        },
    )


def record_push_notification(
    *,
    conversation_id: str,
    title: str,
    body: str,
    priority: str = "normal",
) -> Path:
    """Append a push notification record."""
    return _append(
        "push_notifications",
        {
            "type": "push_notification",
            "ts": time.time(),
            "conversation_id": conversation_id,
            "title": title,
            "body": body,
            "priority": priority,
        },
    )


def record_pr_subscribe_intent(
    *,
    conversation_id: str,
    repository: str,
    events: list[str],
) -> Path:
    """Append a SubscribePR request (stub until host wires GitHub webhooks)."""
    return _append(
        "subscribe_pr",
        {
            "type": "subscribe_pr",
            "ts": time.time(),
            "conversation_id": conversation_id,
            "repository": repository,
            "events": events,
        },
    )


def _read_jsonl_tail(relative_name: str, limit: int) -> list[dict[str, Any]]:
    path = _DIR / relative_name
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def get_inbox_snapshot(
    *,
    limit_per_stream: int = 50,
    streams: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Return recent inbox records for HTTP/API consumers.

    ``streams`` may contain ``file_deliveries``, ``push_notifications``,
    ``subscribe_pr``, or be None for all.
    """
    all_names = ("file_deliveries.jsonl", "push_notifications.jsonl", "subscribe_pr.jsonl")
    key_map = {
        "file_deliveries": "file_deliveries.jsonl",
        "push_notifications": "push_notifications.jsonl",
        "subscribe_pr": "subscribe_pr.jsonl",
    }
    wanted = streams if streams is not None else frozenset(key_map.keys())
    result: dict[str, list[dict[str, Any]]] = {}
    for logical, fname in key_map.items():
        if logical in wanted:
            result[logical] = _read_jsonl_tail(fname, limit_per_stream)
    return result
