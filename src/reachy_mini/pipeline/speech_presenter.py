"""Prepare SDK assistant text for TTS."""

from __future__ import annotations

import re

from reachy_mini.reachy_brain.pipecat_bridge import sdk_message_to_speech_frames

from .frames import (
    InterruptFrame,
    SDKMessageFrame,
    SpeechPresenterFrame,
    TTSStopFrame,
)


class SpeechPresenter:
    """Split SDK assistant text into TTS-ready chunks and handle interrupts."""

    def __init__(
        self,
        *,
        max_chars: int = 120,
        style: dict[str, object] | None = None,
    ) -> None:
        """Create a speech presenter."""
        self.max_chars = max(20, max_chars)
        self.style = dict(style or {})
        self._stopped_turns: set[str] = set()

    async def process(self, frame: object) -> list[object]:
        """Process SDKMessageFrame or InterruptFrame."""
        if isinstance(frame, SDKMessageFrame):
            if frame.turn_id in self._stopped_turns:
                return []
            raw_frames = sdk_message_to_speech_frames(frame, style=self.style)
            chunks: list[str] = []
            for raw in raw_frames:
                chunks.extend(self._split_text(raw.text))
            return [
                SpeechPresenterFrame(
                    text=chunk,
                    style=dict(self.style),
                    turn_id=frame.turn_id,
                    chunk_index=index,
                    is_final=index == len(chunks) - 1,
                )
                for index, chunk in enumerate(chunks)
                if chunk.strip()
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
