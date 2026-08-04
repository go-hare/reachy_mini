"""Brain processor for the v4 SDK-native pipeline."""

from __future__ import annotations

import time
from typing import Any

from reachy_mini.reachy_brain.agent import BrainTurnInput

from .frames import (
    BrowserInputFrame,
    InterruptFrame,
    SDKMessageFrame,
    SpeechActivityFrame,
    TranscriptionFrame,
    TTSAudioFrame,
    VisionEventFrame,
    WorkerEventFrame,
)


class BrainProcessor:
    """Convert completed user turns into SDKMessageFrame streams."""

    def __init__(self, agent: Any) -> None:
        """Create a processor bound to one brain agent (Claude SDK or Pi RPC)."""
        self.agent = agent
        self.context_buffer: list[Any] = []
        self.tts_active = False

    async def process(self, frame: object) -> list[object]:
        """Process one frame and return zero or more output frames."""
        if isinstance(frame, TranscriptionFrame):
            if not frame.is_final:
                self.context_buffer.append(frame)
                return []
            return await self._run_brain(frame.text, frame.turn_id)

        if isinstance(frame, BrowserInputFrame):
            if frame.kind != "text":
                self.context_buffer.append(frame)
                return []
            text = str(frame.payload.get("text", "") or "")
            turn_id = str(frame.payload.get("turn_id", frame.session_id) or frame.session_id)
            return await self._run_brain(text, turn_id)

        if isinstance(frame, VisionEventFrame | WorkerEventFrame):
            self.context_buffer.append(frame)
            return []

        if isinstance(frame, TTSAudioFrame):
            self.tts_active = not frame.is_final
            return []

        if isinstance(frame, SpeechActivityFrame) and frame.state == "start" and self.tts_active:
            self.tts_active = False
            return [
                InterruptFrame(
                    scope="speech",
                    reason="barge_in",
                    ts_ms=frame.ts_ms,
                )
            ]

        return []

    async def _run_brain(self, text: str, turn_id: str) -> list[SDKMessageFrame]:
        start = time.monotonic()
        frames: list[SDKMessageFrame] = []
        turn_input = BrainTurnInput(
            text=text,
            turn_id=turn_id,
            context={"recent_frames": list(self.context_buffer[-8:])},
        )
        async for message in self.agent.run_turn(turn_input):
            frames.append(
                SDKMessageFrame(
                    message=message,
                    turn_id=turn_id,
                    metadata={"latency_ms": int((time.monotonic() - start) * 1000)},
                )
            )
        self.context_buffer.extend(frames)
        return frames
