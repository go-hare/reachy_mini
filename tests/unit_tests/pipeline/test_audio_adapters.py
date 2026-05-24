"""Tests for v4 speech adapters."""

from __future__ import annotations

import pytest

from reachy_mini.reachy_brain.config import SpeechConfig, SpeechInputConfig
from reachy_mini.pipeline.frames import (
    AudioFrame,
    PipelineErrorFrame,
    SpeechPresenterFrame,
    TranscriptionFrame,
    TTSAudioFrame,
    TTSStopFrame,
)
from reachy_mini.pipeline.stt_funasr import FunASRAdapter
from reachy_mini.pipeline.tts_kokoro import KokoroAdapter


@pytest.mark.asyncio
async def test_funasr_adapter_emits_mock_final_transcription() -> None:
    """Mock PCM text payloads become final transcription frames."""
    adapter = FunASRAdapter(SpeechInputConfig(enabled=True, language="zh"))

    frames = await adapter.process(
        AudioFrame(
            pcm="你好".encode("utf-8"),
            sample_rate=16000,
            channels=1,
            ts_ms=1,
        )
    )

    assert len(frames) == 1
    assert isinstance(frames[0], TranscriptionFrame)
    assert frames[0].text == "你好"
    assert frames[0].is_final is True
    assert frames[0].lang == "zh"


@pytest.mark.asyncio
async def test_funasr_adapter_reports_bad_mock_audio() -> None:
    """Invalid mock PCM payloads emit a pipeline error frame."""
    adapter = FunASRAdapter(SpeechInputConfig(enabled=True))

    frames = await adapter.process(
        AudioFrame(pcm=b"\xff", sample_rate=16000, channels=1, ts_ms=1)
    )

    assert len(frames) == 1
    assert isinstance(frames[0], PipelineErrorFrame)
    assert frames[0].component == "stt_funasr"


@pytest.mark.asyncio
async def test_funasr_adapter_ignores_audio_during_playback_cooldown() -> None:
    """Final TTS audio temporarily blocks microphone transcription."""
    now = 1_000
    adapter = FunASRAdapter(
        SpeechInputConfig(enabled=True, playback_block_cooldown_ms=500),
        now_ms=lambda: now,
    )

    await adapter.process(
        TTSAudioFrame(pcm=b"tts", sample_rate=24000, turn_id="t1", is_final=True)
    )
    blocked = await adapter.process(
        AudioFrame(pcm="不该识别".encode("utf-8"), sample_rate=16000, channels=1, ts_ms=now)
    )
    now = 1_501
    unblocked = await adapter.process(
        AudioFrame(pcm="可以识别".encode("utf-8"), sample_rate=16000, channels=1, ts_ms=now)
    )

    assert blocked == []
    assert len(unblocked) == 1
    assert isinstance(unblocked[0], TranscriptionFrame)
    assert unblocked[0].text == "可以识别"


@pytest.mark.asyncio
async def test_kokoro_adapter_emits_tts_audio_and_honors_stop() -> None:
    """Presenter frames become audio unless TTS has been stopped."""
    adapter = KokoroAdapter(SpeechConfig(enabled=True, sample_rate=24000))

    frames = await adapter.process(
        SpeechPresenterFrame(
            text="你好",
            style={"voice": "zf_001"},
            turn_id="t1",
            chunk_index=0,
            is_final=True,
        )
    )
    stopped = await adapter.process(TTSStopFrame(turn_id="t1", reason="barge_in"))
    after_stop = await adapter.process(
        SpeechPresenterFrame(
            text="不会播放",
            style={},
            turn_id="t1",
            chunk_index=1,
            is_final=True,
        )
    )

    assert len(frames) == 1
    assert isinstance(frames[0], TTSAudioFrame)
    assert frames[0].pcm == "你好".encode("utf-8")
    assert frames[0].sample_rate == 24000
    assert stopped == []
    assert after_stop == []
