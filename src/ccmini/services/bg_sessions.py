"""Background task index (reference ``BG_SESSIONS`` / ``claude ps``-style summaries).

When ``MINI_AGENT_BG_SESSIONS_LOG=1``, append a JSON line per completed background
task so hosts can list recent work without scanning full transcripts.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from ..paths import mini_agent_path

logger = logging.getLogger(__name__)

_ENV = "MINI_AGENT_BG_SESSIONS_LOG"


def is_bg_sessions_log_enabled() -> bool:
    v = (os.environ.get(_ENV) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _safe_id(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in s)


def append_bg_session_record(
    *,
    task_id: str,
    name: str,
    status: str,
    success: bool,
    conversation_id: str = "",
    duration_ms: int = 0,
    error: str = "",
) -> None:
    if not is_bg_sessions_log_enabled():
        return
    try:
        base = mini_agent_path("sessions")
        base.mkdir(parents=True, exist_ok=True)
        path = base / "bg_sessions.jsonl"
        line = {
            "ts": time.time(),
            "taskId": task_id,
            "name": name,
            "status": status,
            "success": success,
            "conversationId": conversation_id or "",
            "durationMs": duration_ms,
            "error": error[:2000] if error else "",
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        logger.debug("bg_sessions append failed", exc_info=True)


__all__ = ["append_bg_session_record", "is_bg_sessions_log_enabled"]
