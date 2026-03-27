"""Thin driver that maps assistant audio deltas onto speech-motion helpers."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from reachy_mini.runtime.audio import HeadWobbler


@dataclass(slots=True)
class SpeechDriver:
    """Own the minimal speech-motion bridge for the embodiment layer."""

    head_wobbler: HeadWobbler | Any | None = None
    speech_idle_timeout_s: float = 0.35
    now_fn: Callable[[], float] = time.monotonic
    _speech_active: bool = field(default=False, init=False, repr=False)
    _last_audio_at: float = field(default=0.0, init=False, repr=False)
    _current_phase: str = field(default="idle", init=False, repr=False)

    @property
    def speech_active(self) -> bool:
        """Whether assistant speech motion is currently considered active."""

        return self._speech_active

    @property
    def current_phase(self) -> str:
        """Return the latest speech-driver lifecycle phase."""

        return self._current_phase

    def start(self) -> bool:
        """Start the underlying speech-motion helper when available."""

        head_wobbler = self.head_wobbler
        if head_wobbler is None or not hasattr(head_wobbler, "start"):
            return False
        head_wobbler.start()
        return True

    def stop(self) -> bool:
        """Stop the underlying speech-motion helper and clear runtime state."""

        head_wobbler = self.head_wobbler
        if head_wobbler is None or not hasattr(head_wobbler, "stop"):
            return False
        head_wobbler.stop()
        self._speech_active = False
        self._current_phase = "idle"
        self._last_audio_at = 0.0
        return True

    def apply_phase(self, phase: str) -> bool:
        """Update the reply lifecycle and clear stale speech motion when needed."""

        normalized_phase = str(phase or "").strip().lower() or "idle"
        self._current_phase = normalized_phase
        if normalized_phase != "replying":
            if self._speech_active:
                self.reset_speech_motion()
                return True
            return False

        if self._speech_active and self._speech_has_gone_idle():
            self.reset_speech_motion()
            return True
        return False

    def feed_audio_delta(self, delta_b64: str) -> bool:
        """Feed one assistant PCM delta into the speech-motion helper."""

        payload = str(delta_b64 or "")
        head_wobbler = self.head_wobbler
        if not payload or head_wobbler is None or not hasattr(head_wobbler, "feed"):
            return False
        head_wobbler.feed(payload)
        self._speech_active = True
        self._current_phase = "replying"
        self._last_audio_at = self._current_time()
        return True

    def reset_speech_motion(self) -> bool:
        """Reset queued speech motion and clear the active-speech flag."""

        head_wobbler = self.head_wobbler
        if head_wobbler is None or not hasattr(head_wobbler, "reset"):
            return False
        head_wobbler.reset()
        self._speech_active = False
        self._last_audio_at = 0.0
        return True

    def _current_time(self) -> float:
        return float(self.now_fn())

    def _speech_has_gone_idle(self) -> bool:
        if not self._speech_active:
            return False
        if self._last_audio_at <= 0.0:
            return True
        return (self._current_time() - self._last_audio_at) >= float(
            max(self.speech_idle_timeout_s, 0.0)
        )
