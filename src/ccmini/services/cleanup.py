"""Graceful shutdown and periodic disk cleanup — port of ``utils/cleanup.ts``.

Provides:
- ``CleanupRegistry`` — Central atexit-style registry for cleanup handlers
- ``cleanup_old_sessions()`` — Remove stale session files beyond retention
- ``cleanup_old_logs()`` — Remove old debug/error logs
- ``cleanup_temp_files()`` — Remove temp files from agent operations
- ``run_background_cleanup()`` — Fire-and-forget all cleaners at startup

Usage::

    registry = CleanupRegistry()
    registry.register("mcp", mcp_manager.shutdown)
    registry.register("session", lambda: session_store.save())

    # At shutdown:
    await registry.run_all()
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import shutil
import signal
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..paths import mini_agent_home

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 30
_MINI_AGENT_DIR = mini_agent_home()


# ── Cleanup registry ────────────────────────────────────────────────


@dataclass
class _CleanupEntry:
    name: str
    handler: Callable[[], Any]
    priority: int = 0  # lower runs first


class CleanupRegistry:
    """Central registry for shutdown cleanup handlers.

    Handlers run in priority order (lower = earlier). Async and sync
    handlers are both supported.
    """

    def __init__(self) -> None:
        self._entries: list[_CleanupEntry] = []
        self._ran = False
        self._registered_atexit = False

    def register(
        self,
        name: str,
        handler: Callable[[], Any],
        *,
        priority: int = 50,
    ) -> None:
        """Register a cleanup handler."""
        self._entries.append(_CleanupEntry(name=name, handler=handler, priority=priority))
        self._entries.sort(key=lambda e: e.priority)

        if not self._registered_atexit:
            atexit.register(self._atexit_sync)
            self._registered_atexit = True

    def unregister(self, name: str) -> None:
        self._entries = [e for e in self._entries if e.name != name]

    async def run_all(self, *, timeout: float = 10.0) -> None:
        """Run all cleanup handlers with a total timeout."""
        if self._ran:
            return
        self._ran = True

        for entry in self._entries:
            try:
                result = entry.handler()
                if asyncio.iscoroutine(result):
                    await asyncio.wait_for(result, timeout=timeout / max(len(self._entries), 1))
            except asyncio.TimeoutError:
                logger.warning("Cleanup handler '%s' timed out", entry.name)
            except Exception:
                logger.debug("Cleanup handler '%s' failed", entry.name, exc_info=True)

        logger.debug("All cleanup handlers completed")

    def _atexit_sync(self) -> None:
        """Synchronous atexit fallback for handlers not yet run."""
        if self._ran:
            return
        self._ran = True
        for entry in self._entries:
            try:
                result = entry.handler()
                if asyncio.iscoroutine(result):
                    pass  # Can't await in atexit; skip async handlers
            except Exception:
                pass

    def install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers that trigger cleanup."""
        loop = asyncio.get_event_loop()

        def _handler(sig: int, frame: Any) -> None:
            if not self._ran:
                try:
                    loop.run_until_complete(self.run_all(timeout=5.0))
                except RuntimeError:
                    self._atexit_sync()
            raise SystemExit(128 + sig)

        try:
            signal.signal(signal.SIGINT, _handler)
            signal.signal(signal.SIGTERM, _handler)
        except (ValueError, OSError):
            pass


# Global registry instance
_global_registry: CleanupRegistry | None = None


def get_cleanup_registry() -> CleanupRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = CleanupRegistry()
    return _global_registry


def register_cleanup(
    name: str,
    handler: Callable[[], Any],
    *,
    priority: int = 50,
) -> None:
    """Register a handler on the global cleanup registry."""
    get_cleanup_registry().register(name, handler, priority=priority)


# ── Disk cleanup functions ──────────────────────────────────────────


def _cutoff_time(retention_days: int = DEFAULT_RETENTION_DAYS) -> float:
    return time.time() - (retention_days * 86400)


def cleanup_old_sessions(
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> int:
    """Remove session JSONL files older than retention period.

    Returns number of files removed.
    """
    sessions_dir = _MINI_AGENT_DIR / "sessions"
    if not sessions_dir.exists():
        return 0

    cutoff = _cutoff_time(retention_days)
    removed = 0

    for path in sessions_dir.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            pass

    # Remove empty session directories
    for d in sorted(sessions_dir.iterdir(), reverse=True):
        if d.is_dir():
            try:
                if not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass

    if removed:
        logger.debug("Cleaned up %d old session files", removed)
    return removed


def cleanup_old_logs(
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> int:
    """Remove old debug and error log files."""
    logs_dir = _MINI_AGENT_DIR / "logs"
    if not logs_dir.exists():
        return 0

    cutoff = _cutoff_time(retention_days)
    removed = 0

    for path in logs_dir.rglob("*.log"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            pass

    # Also clean recordings
    recordings_dir = _MINI_AGENT_DIR / "recordings"
    if recordings_dir.exists():
        for path in recordings_dir.rglob("*.cast"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                pass

    if removed:
        logger.debug("Cleaned up %d old log/recording files", removed)
    return removed


def cleanup_temp_files() -> int:
    """Remove temporary files created by agent operations."""
    temp_dir = _MINI_AGENT_DIR / "tmp"
    if not temp_dir.exists():
        return 0

    cutoff = _cutoff_time(1)  # 24h for temp files
    removed = 0

    for path in temp_dir.iterdir():
        try:
            if path.stat().st_mtime < cutoff:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink()
                removed += 1
        except OSError:
            pass

    if removed:
        logger.debug("Cleaned up %d temp files", removed)
    return removed


def cleanup_stale_locks() -> int:
    """Remove stale lock files from dead processes."""
    removed = 0
    for lock_file in _MINI_AGENT_DIR.glob("*.lock"):
        try:
            content = lock_file.read_text().strip()
            pid_str = content.split(":")[0]
            pid = int(pid_str)

            try:
                os.kill(pid, 0)
            except (OSError, ProcessLookupError):
                lock_file.unlink()
                removed += 1
        except (ValueError, OSError):
            try:
                if lock_file.stat().st_mtime < _cutoff_time(1):
                    lock_file.unlink()
                    removed += 1
            except OSError:
                pass

    if removed:
        logger.debug("Cleaned up %d stale lock files", removed)
    return removed


def cleanup_nurture_orphans() -> int:
    """Remove orphaned buddy nurture/companion data."""
    removed = 0
    for name in ("nurture.json", "companion.json"):
        p = _MINI_AGENT_DIR / name
        if p.exists():
            try:
                if p.stat().st_size == 0:
                    p.unlink()
                    removed += 1
            except OSError:
                pass
    return removed


# ── Background cleanup orchestrator ──────────────────────────────────


async def run_background_cleanup(
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> dict[str, int]:
    """Run all cleanup tasks in the background.

    Called once at startup. Returns counts per cleaner.
    """
    results: dict[str, int] = {}

    loop = asyncio.get_event_loop()

    cleaners: list[tuple[str, Callable[[], int]]] = [
        ("sessions", lambda: cleanup_old_sessions(retention_days)),
        ("logs", lambda: cleanup_old_logs(retention_days)),
        ("temp", cleanup_temp_files),
        ("locks", cleanup_stale_locks),
        ("nurture", cleanup_nurture_orphans),
    ]

    for name, cleaner in cleaners:
        try:
            count = await loop.run_in_executor(None, cleaner)
            results[name] = count
        except Exception:
            logger.debug("Background cleanup '%s' failed", name, exc_info=True)
            results[name] = 0

    total = sum(results.values())
    if total > 0:
        logger.info("Background cleanup removed %d items: %s", total, results)

    return results
