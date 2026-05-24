"""Thin Kokoro adapter for the v4 pipeline mock path."""

from __future__ import annotations

from reachy_mini.reachy_brain.config import SpeechConfig

from .frames import SpeechPresenterFrame, TTSAudioFrame, TTSStopFrame


class KokoroAdapter:
    """Mockable TTS adapter that emits deterministic audio frames in Phase 1."""

    def __init__(self, config: SpeechConfig) -> None:
        """Create a Kokoro adapter."""
        self.config = config
        self.stopped = False

    async def process(self, frame: object) -> list[object]:
        """Convert presenter frames to TTS audio frames."""
        if isinstance(frame, TTSStopFrame):
            self.stopped = True
            return []
        if isinstance(frame, SpeechPresenterFrame):
            if not self.config.enabled or self.stopped:
                return []
            pcm = frame.text.encode("utf-8")
            return [
                TTSAudioFrame(
                    pcm=pcm,
                    sample_rate=self.config.sample_rate,
                    turn_id=frame.turn_id,
                    is_final=frame.is_final,
                )
            ]
        return []
