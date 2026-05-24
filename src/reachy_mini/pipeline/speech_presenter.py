"""Prepare Brain replies for TTS."""

from __future__ import annotations

import re

from .frames import (
    BrainReplyFrame,
    InterruptFrame,
    SpeechPresenterFrame,
    TTSStopFrame,
)


class SpeechPresenter:
    """Split reply text into TTS-ready chunks and handle interrupts."""

    def __init__(self, *, max_chars: int = 120) -> None:
        """Create a speech presenter."""
        self.max_chars = max(20, max_chars)
        self._stopped_turns: set[str] = set()

    async def process(self, frame: object) -> list[object]:
        """Process BrainReplyFrame or InterruptFrame."""
        if isinstance(frame, BrainReplyFrame):
            if not frame.reply_text.strip():
                return []
            chunks = self._split_text(frame.reply_text)
            return [
                SpeechPresenterFrame(
                    text=chunk,
                    style=dict(frame.speech_style),
                    turn_id=frame.turn_id,
                    chunk_index=index,
                    is_final=index == len(chunks) - 1,
                )
                for index, chunk in enumerate(chunks)
                if frame.turn_id not in self._stopped_turns
            ]

        if isinstance(frame, InterruptFrame) and frame.scope in {"speech", "all"}:
            if frame.turn_id:
                self._stopped_turns.add(frame.turn_id)
            return [TTSStopFrame(turn_id=frame.turn_id, reason=frame.reason)]

        return []

    def _split_text(self, text: str) -> list[str]:
        parts = [
            item.strip()
            for item in re.split(r"(?<=[。！？!?；;])\s*", text.strip())
            if item.strip()
        ]
        if not parts:
            return []
        chunks: list[str] = []
        for part in parts:
            if len(part) <= self.max_chars:
                chunks.append(part)
                continue
            chunks.extend(
                part[index : index + self.max_chars]
                for index in range(0, len(part), self.max_chars)
            )
        return chunks
