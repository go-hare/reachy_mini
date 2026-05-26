"""Tests for the v4 live IO adapters."""

from __future__ import annotations

import pytest

from reachy_mini.pipeline.frames import AudioFrame, TTSAudioFrame
from reachy_mini.pipeline.live_io import (
    CallableSpeakerSink,
    MicrophoneSource,
    SpeakerSink,
)


@pytest.mark.asyncio
async def test_microphone_source_delivers_frames_to_callback() -> None:
    """MicrophoneSource.deliver wraps PCM into an AudioFrame and forwards it."""
    received: list[AudioFrame] = []

    async def on_frame(frame: AudioFrame) -> None:
        received.append(frame)

    source = MicrophoneSource(sample_rate=16000, channels=1)
    await source.start(on_frame)
    await source.deliver(b"\x00\x00\x00\x01", ts_ms=125)
    await source.stop()

    assert len(received) == 1
    assert received[0].pcm == b"\x00\x00\x00\x01"
    assert received[0].sample_rate == 16000
    assert received[0].channels == 1
    assert received[0].ts_ms == 125


@pytest.mark.asyncio
async def test_microphone_source_drops_frames_after_stop() -> None:
    """deliver() after stop() is silently ignored."""
    received: list[AudioFrame] = []

    async def on_frame(frame: AudioFrame) -> None:
        received.append(frame)

    source = MicrophoneSource()
    await source.start(on_frame)
    await source.stop()
    await source.deliver(b"\x00\x00")

    assert received == []


@pytest.mark.asyncio
async def test_speaker_sink_buffers_frames_and_drops_after_stop() -> None:
    """SpeakerSink keeps a buffer until stop() is called."""
    sink = SpeakerSink()
    await sink.start()
    await sink.consume(TTSAudioFrame(pcm=b"a", sample_rate=24000, turn_id="T", is_final=False))
    await sink.consume(TTSAudioFrame(pcm=b"b", sample_rate=24000, turn_id="T", is_final=True))
    assert len(sink.frames) == 2
    await sink.stop()
    await sink.consume(TTSAudioFrame(pcm=b"c", sample_rate=24000, turn_id="T", is_final=False))
    assert len(sink.frames) == 2


@pytest.mark.asyncio
async def test_callable_speaker_sink_forwards_each_chunk() -> None:
    """CallableSpeakerSink delegates each chunk to its callback."""
    seen: list[TTSAudioFrame] = []

    async def callback(frame: TTSAudioFrame) -> None:
        seen.append(frame)

    sink = CallableSpeakerSink(callback)
    await sink.start()
    await sink.consume(TTSAudioFrame(pcm=b"x", sample_rate=24000, turn_id="T", is_final=True))
    assert len(seen) == 1
    await sink.stop()
    await sink.consume(TTSAudioFrame(pcm=b"y", sample_rate=24000, turn_id="T", is_final=True))
    assert len(seen) == 1
