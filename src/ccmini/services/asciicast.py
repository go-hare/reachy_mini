"""Asciicast — terminal recording in asciicast v2 format.

Ported from Claude Code's ``utils/asciicast.ts``.

Records terminal output in the `asciicast v2 format
<https://docs.asciinema.org/manual/asciicast/v2/>`_ for playback
with ``asciinema play`` or upload to asciinema.org.

Features:
- Monkey-patches ``sys.stdout.write`` to capture output with timestamps
- Buffered writes (flush every 500ms or 50 events)
- Tracks terminal resize events
- Session-aware file naming
- Cleanup on exit

Usage::

    recorder = AsciicastRecorder(session_id="abc123")
    recorder.install()
    # ... terminal output is captured ...
    recorder.flush()
    recorder.uninstall()
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from ..paths import mini_agent_path

# ── Constants ────────────────────────────────────────────────────────

FLUSH_INTERVAL_S = 0.5
MAX_BUFFER_ITEMS = 50
MAX_BUFFER_BYTES = 10 * 1024 * 1024  # 10 MB

_RECORD_DIR = mini_agent_path("recordings")


# ── Asciicast v2 header ─────────────────────────────────────────────

def _build_header(
    width: int = 80,
    height: int = 24,
    *,
    title: str = "",
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build an asciicast v2 header object."""
    header: dict[str, Any] = {
        "version": 2,
        "width": width,
        "height": height,
        "timestamp": int(time.time()),
    }
    if title:
        header["title"] = title
    header["env"] = env or {
        "SHELL": os.environ.get("SHELL", "/bin/bash"),
        "TERM": os.environ.get("TERM", "xterm-256color"),
    }
    return header


# ── Recorder ─────────────────────────────────────────────────────────

@dataclass
class AsciicastRecorder:
    """Records terminal output in asciicast v2 format.

    Parameters
    ----------
    session_id:
        Unique identifier for the recording session.
    cwd:
        Working directory (used in file path).
    title:
        Optional title for the recording.
    """

    session_id: str = ""
    cwd: str = ""
    title: str = ""

    _file_path: Path | None = field(default=None, init=False, repr=False)
    _start_time: float = field(default=0.0, init=False, repr=False)
    _buffer: list[str] = field(default_factory=list, init=False, repr=False)
    _buffer_bytes: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _flush_timer: threading.Timer | None = field(default=None, init=False, repr=False)
    _original_write: Any = field(default=None, init=False, repr=False)
    _installed: bool = field(default=False, init=False, repr=False)
    _event_count: int = field(default=0, init=False, repr=False)

    @property
    def file_path(self) -> Path | None:
        return self._file_path

    @property
    def is_installed(self) -> bool:
        return self._installed

    @property
    def event_count(self) -> int:
        return self._event_count

    # ── Lifecycle ────────────────────────────────────────────────

    def install(self) -> Path:
        """Start recording by monkey-patching stdout.

        Returns the path to the recording file.
        """
        if self._installed:
            assert self._file_path is not None
            return self._file_path

        self._file_path = self._build_path()
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._start_time = time.monotonic()

        # Write header
        width, height = _get_terminal_size()
        header = _build_header(width, height, title=self.title)
        self._file_path.write_text(
            json.dumps(header) + "\n",
            encoding="utf-8",
        )

        # Patch stdout
        self._original_write = sys.stdout.write
        sys.stdout.write = self._capture_write  # type: ignore[assignment]

        self._installed = True
        self._schedule_flush()

        atexit.register(self._cleanup)
        return self._file_path

    def uninstall(self) -> None:
        """Stop recording and restore stdout."""
        if not self._installed:
            return

        self._installed = False

        if self._original_write is not None:
            sys.stdout.write = self._original_write  # type: ignore[assignment]
            self._original_write = None

        self._cancel_flush_timer()
        self.flush()
        atexit.unregister(self._cleanup)

    def flush(self) -> None:
        """Flush buffered events to disk."""
        with self._lock:
            if not self._buffer or self._file_path is None:
                return
            data = "".join(self._buffer)
            self._buffer.clear()
            self._buffer_bytes = 0

        try:
            with self._file_path.open("a", encoding="utf-8") as f:
                f.write(data)
        except Exception:
            pass

    def record_resize(self, width: int, height: int) -> None:
        """Record a terminal resize event."""
        if not self._installed:
            return
        elapsed = time.monotonic() - self._start_time
        event = json.dumps([elapsed, "r", f"{width}x{height}"])
        self._append_event(event + "\n")

    # ── Capture ──────────────────────────────────────────────────

    def _capture_write(self, text: str) -> int:
        """Replacement for ``sys.stdout.write`` that captures output."""
        if self._installed and text:
            elapsed = time.monotonic() - self._start_time
            event = json.dumps([elapsed, "o", text])
            self._append_event(event + "\n")

        if self._original_write is not None:
            return self._original_write(text)
        return len(text)

    def _append_event(self, line: str) -> None:
        with self._lock:
            self._buffer.append(line)
            self._buffer_bytes += len(line)
            self._event_count += 1

            if (
                len(self._buffer) >= MAX_BUFFER_ITEMS
                or self._buffer_bytes >= MAX_BUFFER_BYTES
            ):
                self._flush_unlocked()

    def _flush_unlocked(self) -> None:
        """Flush without acquiring lock (caller must hold it)."""
        if not self._buffer or self._file_path is None:
            return
        data = "".join(self._buffer)
        self._buffer.clear()
        self._buffer_bytes = 0
        try:
            with self._file_path.open("a", encoding="utf-8") as f:
                f.write(data)
        except Exception:
            pass

    # ── Timer ────────────────────────────────────────────────────

    def _schedule_flush(self) -> None:
        if not self._installed:
            return
        self._flush_timer = threading.Timer(FLUSH_INTERVAL_S, self._timed_flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _timed_flush(self) -> None:
        self.flush()
        if self._installed:
            self._schedule_flush()

    def _cancel_flush_timer(self) -> None:
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None

    # ── Path management ──────────────────────────────────────────

    def _build_path(self) -> Path:
        """Build the recording file path."""
        session = self.session_id or "unknown"
        ts = int(time.time())
        filename = f"{session}-{ts}.cast"

        if self.cwd:
            sanitized = self.cwd.replace("/", "_").replace("\\", "_").strip("_")
            return _RECORD_DIR / sanitized / filename

        return _RECORD_DIR / filename

    def rename_for_session(self, new_session_id: str) -> Path | None:
        """Rename recording file when session ID changes (e.g., resume)."""
        if self._file_path is None or not self._file_path.exists():
            return None

        self.flush()
        old_path = self._file_path
        self.session_id = new_session_id
        new_path = old_path.parent / f"{new_session_id}-{int(time.time())}.cast"

        try:
            old_path.rename(new_path)
            self._file_path = new_path
            return new_path
        except OSError:
            return None

    # ── Cleanup ──────────────────────────────────────────────────

    def _cleanup(self) -> None:
        self.uninstall()


# ── Helpers ──────────────────────────────────────────────────────────


def _get_terminal_size() -> tuple[int, int]:
    """Get current terminal size, defaulting to 80x24."""
    try:
        size = os.get_terminal_size()
        return size.columns, size.lines
    except (ValueError, OSError):
        return 80, 24


def get_session_recordings(session_id: str) -> list[Path]:
    """Find all recordings for a session ID."""
    recordings: list[Path] = []
    if not _RECORD_DIR.exists():
        return recordings
    for cast_file in _RECORD_DIR.rglob(f"{session_id}*.cast"):
        recordings.append(cast_file)
    return sorted(recordings)
