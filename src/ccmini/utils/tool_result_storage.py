"""Tool Result Storage — persist large tool outputs to disk.

When a tool result exceeds a configurable character threshold, the full
text is written to ``~/.mini_agent/tool_results/`` and the in-message
content is replaced with a compact reference (preview + path).  This
keeps token counts manageable while preserving the ability to restore
the full result when needed.

Thread safety is achieved via :class:`threading.Lock` for in-process
operations and optional file-level locking (``fcntl`` on Unix,
``msvcrt`` on Windows) for cross-process safety.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import mini_agent_path

log = logging.getLogger(__name__)

_STORAGE_DIR = mini_agent_path("tool_results")
_DEFAULT_THRESHOLD = 10_000  # chars
_MAX_AGE_SECONDS = 86_400  # 24h
_PREVIEW_SIZE = 200

_PERSISTED_TAG = "<persisted-output>"
_PERSISTED_END_TAG = "</persisted-output>"
_CLEARED_MSG = "[Old tool result content cleared]"


@dataclass(slots=True)
class StoredResult:
    key: str
    path: str
    original_size: int
    preview: str
    has_more: bool
    stored_at: float = field(default_factory=time.time)


def _hash_id(tool_use_id: str) -> str:
    return hashlib.sha256(tool_use_id.encode()).hexdigest()[:16]


def _lock_file(fp: Any) -> None:
    """Platform-aware advisory file lock (best-effort)."""
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        pass  # advisory — proceed without lock


def _unlock_file(fp: Any) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fp, fcntl.LOCK_UN)
    except Exception:
        pass


class ToolResultStore:
    """Manages persisted tool results on disk.

    Provides store / retrieve / cleanup operations.  Results are keyed
    by a hash of the ``tool_use_id`` and stored as plain-text files.
    """

    def __init__(self, storage_dir: Path | None = None) -> None:
        self._dir = storage_dir or _STORAGE_DIR
        self._lock = threading.Lock()
        self._ensure_dir()
        self._cleanup_old_on_startup()

    def _ensure_dir(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.debug("Could not create tool results directory %s", self._dir, exc_info=True)

    def _cleanup_old_on_startup(self) -> None:
        """Remove results older than 24 h at startup."""
        self.clear_old(_MAX_AGE_SECONDS)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def store(self, tool_use_id: str, result_text: str) -> str:
        """Persist *result_text* and return the storage key."""
        key = _hash_id(tool_use_id)
        path = self._dir / f"{key}.txt"

        with self._lock:
            try:
                with open(path, "w", encoding="utf-8") as fp:
                    _lock_file(fp)
                    fp.write(result_text)
                    _unlock_file(fp)
                log.debug("Stored tool result %s (%d chars) → %s", key, len(result_text), path)
            except OSError:
                log.warning("Failed to store tool result %s", key, exc_info=True)
        return key

    def retrieve(self, key: str) -> str | None:
        """Return the full stored text for *key*, or None if not found."""
        path = self._dir / f"{key}.txt"
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError:
            log.warning("Failed to read tool result %s", key, exc_info=True)
            return None

    def clear_old(self, max_age_seconds: float = _MAX_AGE_SECONDS) -> int:
        """Remove result files older than *max_age_seconds*.  Returns count."""
        removed = 0
        cutoff = time.time() - max_age_seconds
        try:
            for entry in self._dir.iterdir():
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
                    removed += 1
        except OSError:
            pass
        if removed:
            log.debug("Cleaned up %d old tool results", removed)
        return removed

    def get_summary(self, key: str) -> str:
        """Return a short preview string suitable for inline display."""
        text = self.retrieve(key)
        if text is None:
            return f"[Tool result {key} not found on disk]"
        if len(text) <= _PREVIEW_SIZE:
            return text
        return f"{text[:_PREVIEW_SIZE]}... ({len(text)} chars stored on disk)"

    def get_stored_result(self, tool_use_id: str, result_text: str) -> StoredResult:
        """Store *result_text* and return a :class:`StoredResult` with preview."""
        key = self.store(tool_use_id, result_text)
        preview, has_more = _generate_preview(result_text, _PREVIEW_SIZE)
        return StoredResult(
            key=key,
            path=str(self._dir / f"{key}.txt"),
            original_size=len(result_text),
            preview=preview,
            has_more=has_more,
        )


def should_store(result_text: str, threshold: int = _DEFAULT_THRESHOLD) -> bool:
    """Return True when *result_text* exceeds *threshold* characters."""
    return len(result_text) > threshold


def replace_with_reference(result_text: str, stored: StoredResult) -> str:
    """Build a compact reference message to replace a large tool result."""
    msg = f"{_PERSISTED_TAG}\n"
    msg += f"Output too large ({stored.original_size} chars). Full output saved to: {stored.path}\n\n"
    msg += f"Preview (first {_PREVIEW_SIZE} chars):\n"
    msg += stored.preview
    msg += "\n...\n" if stored.has_more else "\n"
    msg += _PERSISTED_END_TAG
    return msg


def restore_from_references(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk *messages* and restore any disk-referenced tool results in-place.

    Only touches ``tool_result`` blocks whose content starts with
    ``<persisted-output>``.  Falls back silently if the file is gone.
    """
    store = ToolResultStore()
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") != "tool_result":
                continue
            text = block.get("content", "")
            if not isinstance(text, str) or not text.startswith(_PERSISTED_TAG):
                continue
            # Extract path from the reference
            for line in text.splitlines():
                if "Full output saved to:" in line:
                    path_str = line.split("Full output saved to:")[-1].strip()
                    try:
                        restored = Path(path_str).read_text(encoding="utf-8")
                        block["content"] = restored
                    except OSError:
                        pass
                    break
    return messages


def _generate_preview(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    truncated = text[:max_chars]
    last_nl = truncated.rfind("\n")
    cut = last_nl if last_nl > max_chars * 0.5 else max_chars
    return text[:cut], True
