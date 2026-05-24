"""Thin FunASR adapter for the v4 pipeline mock path."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from reachy_mini.reachy_brain.config import SpeechInputConfig

from .frames import AudioFrame, PipelineErrorFrame, TranscriptionFrame, TTSAudioFrame


class FunASRAdapter:
    """Mockable STT adapter with FunASR-compatible frame behavior."""

    def __init__(
        self,
        config: SpeechInputConfig,
        *,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        """Create a FunASR adapter."""
        self.config = config
        self._now_ms = now_ms or self._monotonic_ms
        self._blocked_until_ms = 0

    async def process(self, frame: object) -> list[object]:
        """Convert mock PCM payloads to transcription frames."""
        if isinstance(frame, TTSAudioFrame) and frame.is_final:
            self._blocked_until_ms = (
                self._now_ms() + self.config.playback_block_cooldown_ms
            )
            return []
        if isinstance(frame, AudioFrame):
            if not self.config.enabled:
                return []
            if self._now_ms() < self._blocked_until_ms:
                return []
            try:
                text = frame.pcm.decode("utf-8").strip()
            except UnicodeDecodeError:
                return [
                    PipelineErrorFrame(
                        component="stt_funasr",
                        reason="mock pcm payload is not utf-8 text",
                    )
                ]
            if not text:
                return []
            return [
                TranscriptionFrame(
                    text=text,
                    is_final=True,
                    turn_id=f"turn_{uuid.uuid4().hex}",
                    lang=self.config.language,
                )
            ]
        return []

    @staticmethod
    def _monotonic_ms() -> int:
        """Return monotonic time in milliseconds."""
        return int(time.monotonic() * 1000)
