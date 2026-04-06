"""Prevent Sleep — cross-platform sleep prevention during long tasks.

Ported from Claude Code's ``services/preventSleep.ts``.

Prevents the system from sleeping while the agent is actively working
(API calls, tool execution). Uses platform-specific mechanisms:

- **macOS**: ``caffeinate -i`` (prevent idle sleep)
- **Linux**: ``systemd-inhibit`` or ``caffeine`` or ``xdg-screensaver``
- **Windows**: ``SetThreadExecutionState`` via ctypes

Reference-counted: multiple callers can ``start()`` and sleep
prevention only stops when all have called ``stop()``.
"""

from __future__ import annotations

import atexit
import ctypes
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

_CAFFEINATE_TIMEOUT = 300  # 5 minutes
_RESTART_INTERVAL = 240    # 4 minutes (restart before timeout)

# Windows SetThreadExecutionState flags
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002

# ── State ────────────────────────────────────────────────────────────

_lock = threading.Lock()
_ref_count = 0
_process: subprocess.Popen[Any] | None = None
_timer: threading.Timer | None = None
_windows_thread: threading.Thread | None = None
_windows_stop_event = threading.Event()
_cleanup_registered = False


# ── Public API ───────────────────────────────────────────────────────


def start_prevent_sleep() -> None:
    """Increment reference count and start sleep prevention if needed."""
    global _ref_count
    with _lock:
        _ref_count += 1
        if _ref_count == 1:
            _start()
            _ensure_cleanup()


def stop_prevent_sleep() -> None:
    """Decrement reference count and stop if zero."""
    global _ref_count
    with _lock:
        if _ref_count <= 0:
            return
        _ref_count -= 1
        if _ref_count == 0:
            _stop()


def force_stop() -> None:
    """Force-stop regardless of reference count."""
    global _ref_count
    with _lock:
        _ref_count = 0
        _stop()


def is_active() -> bool:
    """Whether sleep prevention is currently active."""
    return _ref_count > 0


# ── Platform dispatch ────────────────────────────────────────────────


def _start() -> None:
    system = platform.system()
    if system == "Darwin":
        _start_macos()
    elif system == "Linux":
        _start_linux()
    elif system == "Windows":
        _start_windows()
    else:
        logger.debug("Sleep prevention not supported on %s", system)


def _stop() -> None:
    system = platform.system()
    if system == "Darwin":
        _stop_macos()
    elif system == "Linux":
        _stop_linux()
    elif system == "Windows":
        _stop_windows()


# ── macOS: caffeinate ────────────────────────────────────────────────


def _start_macos() -> None:
    global _process, _timer
    _kill_process()
    _cancel_timer()

    try:
        _process = subprocess.Popen(
            ["caffeinate", "-i", "-t", str(_CAFFEINATE_TIMEOUT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.debug("caffeinate started (pid=%d)", _process.pid)
    except FileNotFoundError:
        logger.debug("caffeinate not found")
        return

    _timer = threading.Timer(_RESTART_INTERVAL, _restart_macos)
    _timer.daemon = True
    _timer.start()


def _restart_macos() -> None:
    """Restart caffeinate before its timeout expires."""
    with _lock:
        if _ref_count > 0:
            _start_macos()


def _stop_macos() -> None:
    _kill_process()
    _cancel_timer()


# ── Linux: systemd-inhibit / caffeine / xdg-screensaver ─────────────


def _start_linux() -> None:
    global _process
    _kill_process()

    if shutil.which("systemd-inhibit"):
        try:
            _process = subprocess.Popen(
                [
                    "systemd-inhibit",
                    "--what=idle",
                    "--who=mini-agent",
                    "--why=Agent is working",
                    "sleep", str(_CAFFEINATE_TIMEOUT),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.debug("systemd-inhibit started (pid=%d)", _process.pid)
            return
        except Exception:
            pass

    if shutil.which("caffeine"):
        try:
            _process = subprocess.Popen(
                ["caffeine"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.debug("caffeine started (pid=%d)", _process.pid)
            return
        except Exception:
            pass

    if shutil.which("xdg-screensaver"):
        try:
            subprocess.run(
                ["xdg-screensaver", "suspend", str(os.getpid())],
                capture_output=True,
                timeout=5,
            )
            logger.debug("xdg-screensaver suspended")
        except Exception:
            pass


def _stop_linux() -> None:
    _kill_process()
    if shutil.which("xdg-screensaver"):
        try:
            subprocess.run(
                ["xdg-screensaver", "resume", str(os.getpid())],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass


# ── Windows: SetThreadExecutionState ─────────────────────────────────


def _start_windows() -> None:
    global _windows_thread
    _stop_windows()

    _windows_stop_event.clear()

    def _keep_awake() -> None:
        try:
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            while not _windows_stop_event.is_set():
                kernel32.SetThreadExecutionState(
                    _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
                )
                _windows_stop_event.wait(timeout=60)
            kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        except Exception as exc:
            logger.debug("Windows sleep prevention error: %s", exc)

    _windows_thread = threading.Thread(target=_keep_awake, daemon=True)
    _windows_thread.start()
    logger.debug("Windows sleep prevention started")


def _stop_windows() -> None:
    global _windows_thread
    _windows_stop_event.set()
    if _windows_thread is not None:
        _windows_thread.join(timeout=5)
        _windows_thread = None
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
    except Exception:
        pass


# ── Helpers ──────────────────────────────────────────────────────────


def _kill_process() -> None:
    global _process
    if _process is not None:
        try:
            _process.terminate()
            _process.wait(timeout=3)
        except Exception:
            try:
                _process.kill()
            except Exception:
                pass
        _process = None


def _cancel_timer() -> None:
    global _timer
    if _timer is not None:
        _timer.cancel()
        _timer = None


def _ensure_cleanup() -> None:
    global _cleanup_registered
    if not _cleanup_registered:
        atexit.register(force_stop)
        _cleanup_registered = True
