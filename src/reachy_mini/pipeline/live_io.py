"""Live audio IO adapters that bridge runtime hardware to the v4 pipeline."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Awaitable, Callable

from .frames import AudioFrame, TTSAudioFrame

LOGGER = logging.getLogger(__name__)

AudioFrameCallback = Callable[[AudioFrame], Awaitable[None]]


class MicrophoneSource:
    """Push raw mic PCM frames to a callback.

    Phase 2 ships a passive default that does not own a hardware mic — concrete
    deployments inject a robot or browser mic by overriding ``start``. The fake
    implementation keeps tests and headless launches honest.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1600,
    ) -> None:
        """Configure the default mic shape."""
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self._callback: AudioFrameCallback | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self, on_frame: AudioFrameCallback) -> None:
        """Begin streaming AudioFrames to ``on_frame``.

        The default implementation does not actually capture audio; it only
        records the callback so subclasses or live wiring can call ``deliver``.
        """
        self._callback = on_frame
        self._stop_event = asyncio.Event()

    async def deliver(self, pcm: bytes, *, ts_ms: int = 0) -> None:
        """Manually deliver one PCM payload as an AudioFrame."""
        callback = self._callback
        if callback is None:
            return
        frame = AudioFrame(
            pcm=pcm,
            sample_rate=self.sample_rate,
            channels=self.channels,
            ts_ms=ts_ms,
        )
        await callback(frame)

    async def stop(self) -> None:
        """Stop streaming and release the callback reference."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, BaseException):
                await self._task
            self._task = None
        self._callback = None
        self._stop_event = None


class SpeakerSink:
    """Consume TTSAudioFrame chunks; default to in-memory queue for tests."""

    def __init__(self) -> None:
        """Initialise an idle sink."""
        self.frames: list[TTSAudioFrame] = []
        self._stopped = False

    async def start(self) -> None:
        """Reset internal state and prepare for new audio."""
        self._stopped = False
        self.frames.clear()

    async def stop(self) -> None:
        """Mark the sink as stopped; further consume calls are dropped."""
        self._stopped = True

    async def consume(self, frame: TTSAudioFrame) -> None:
        """Buffer one TTS chunk for downstream playback."""
        if self._stopped:
            return
        self.frames.append(frame)


class CallableSpeakerSink(SpeakerSink):
    """SpeakerSink that forwards every chunk to a callback."""

    def __init__(self, callback: Callable[[TTSAudioFrame], Awaitable[None]]) -> None:
        """Bind a forwarder coroutine."""
        super().__init__()
        self._callback = callback

    async def consume(self, frame: TTSAudioFrame) -> None:
        """Forward the chunk to the configured callback."""
        if self._stopped:
            return
        await self._callback(frame)


__all__ = [
    "AudioFrameCallback",
    "CallableSpeakerSink",
    "MicrophoneSource",
    "SpeakerSink",
]
