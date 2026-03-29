"""Tests for robot microphone speech-input helpers."""

import asyncio

import numpy as np

from reachy_mini.runtime.config import SpeechInputRuntimeConfig
from reachy_mini.runtime.speech_input import (
    MLXWhisperSpeechInputTranscriber,
    OpenAISpeechInputTranscriber,
    RuntimeMicrophoneBridge,
    build_runtime_speech_input_transcriber,
)


class FakeMedia:
    """Small media stub exposing only the recording API used by the bridge."""

    def __init__(self, frames: list[np.ndarray], sample_rate_hz: int = 16_000) -> None:
        self.frames = list(frames)
        self.sample_rate_hz = sample_rate_hz
        self.started = 0
        self.stopped = 0

    def start_recording(self) -> None:
        self.started += 1

    def stop_recording(self) -> None:
        self.stopped += 1

    def get_input_audio_samplerate(self) -> int:
        return self.sample_rate_hz

    def get_audio_sample(self) -> np.ndarray | None:
        if not self.frames:
            return None
        return self.frames.pop(0)


class FakeLogger:
    """Collect warning/debug messages without requiring a configured logger."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def warning(self, message: str, *args: object) -> None:
        self.messages.append(message % args if args else message)

    def debug(self, message: str, *args: object) -> None:
        self.messages.append(message % args if args else message)


class FakeTranscriber:
    """Return one deterministic transcript for every utterance."""

    def __init__(self, transcript: str = "你好") -> None:
        self.transcript = transcript
        self.calls: list[tuple[bytes, int]] = []

    async def transcribe_pcm16(self, pcm16: bytes, sample_rate_hz: int) -> str:
        self.calls.append((bytes(pcm16), int(sample_rate_hz)))
        return self.transcript


def test_build_runtime_speech_input_transcriber_supports_openai() -> None:
    """OpenAI speech-input provider should build the matching transcriber."""

    transcriber = build_runtime_speech_input_transcriber(
        config=SpeechInputRuntimeConfig(
            enabled=True,
            provider="openai",
            model="gpt-4o-mini-transcribe",
            api_key="demo-key",
        )
    )

    assert isinstance(transcriber, OpenAISpeechInputTranscriber)


def test_build_runtime_speech_input_transcriber_supports_mlx_whisper() -> None:
    """Local MLX Whisper provider should build the matching transcriber."""

    transcriber = build_runtime_speech_input_transcriber(
        config=SpeechInputRuntimeConfig(
            enabled=True,
            provider="mlx_whisper",
            model="mlx-community/whisper-small-mlx",
        )
    )

    assert isinstance(transcriber, MLXWhisperSpeechInputTranscriber)


def test_runtime_microphone_bridge_emits_started_stopped_and_user_text() -> None:
    """Robot microphone bridge should map one utterance into runtime speech events."""

    silence = np.zeros(320, dtype=np.float32)
    speech = np.full(320, 0.25, dtype=np.float32)
    frames = [silence] * 2 + [speech] * 6 + [silence] * 40
    media = FakeMedia(frames)
    logger = FakeLogger()
    transcriber = FakeTranscriber("你好，Reachy")
    config = SpeechInputRuntimeConfig(
        enabled=True,
        provider="mlx_whisper",
        vad_db_on=-35.0,
        vad_db_off=-45.0,
        vad_attack_ms=60,
        vad_release_ms=300,
        min_utterance_ms=120,
        max_utterance_ms=5_000,
    )

    events: list[tuple[str, str]] = []
    transcript_ready = asyncio.Event()

    async def on_speech_started(_: str) -> None:
        events.append(("started", ""))

    async def on_speech_stopped(_: str) -> None:
        events.append(("stopped", ""))

    async def on_user_text(text: str) -> None:
        events.append(("user_text", text))
        transcript_ready.set()

    bridge = RuntimeMicrophoneBridge(
        media=media,
        transcriber=transcriber,
        config=config,
        logger=logger,  # type: ignore[arg-type]
        on_speech_started=on_speech_started,
        on_speech_stopped=on_speech_stopped,
        on_user_text=on_user_text,
    )

    async def _run_bridge() -> None:
        task = asyncio.create_task(bridge.run())
        await asyncio.wait_for(transcript_ready.wait(), timeout=3.0)
        await bridge.stop()
        await asyncio.wait_for(task, timeout=3.0)

    asyncio.run(_run_bridge())

    assert media.started == 1
    assert media.stopped == 1
    assert events[:2] == [("started", ""), ("stopped", "")]
    assert events[-1] == ("user_text", "你好，Reachy")
    assert len(transcriber.calls) == 1
    assert transcriber.calls[0][1] == 16_000
    assert transcriber.calls[0][0]


def test_runtime_microphone_bridge_ignores_input_while_assistant_audio_is_active() -> None:
    """Robot microphone bridge should not start a new capture while assistant audio is active."""

    speech = np.full(320, 0.25, dtype=np.float32)
    media = FakeMedia([speech] * 20)
    logger = FakeLogger()
    transcriber = FakeTranscriber("这句不该被送出")
    events: list[tuple[str, str]] = []

    bridge = RuntimeMicrophoneBridge(
        media=media,
        transcriber=transcriber,
        config=SpeechInputRuntimeConfig(
            enabled=True,
            provider="mlx_whisper",
            vad_db_on=-35.0,
            vad_db_off=-45.0,
            vad_attack_ms=60,
            vad_release_ms=300,
            min_utterance_ms=120,
            max_utterance_ms=5_000,
        ),
        logger=logger,  # type: ignore[arg-type]
        on_speech_started=lambda _: events.append(("started", "")),
        on_speech_stopped=lambda _: events.append(("stopped", "")),
        on_user_text=lambda text: events.append(("user_text", text)),
        input_blocked=lambda: True,
    )

    async def _run_bridge() -> None:
        task = asyncio.create_task(bridge.run())
        await asyncio.sleep(0.2)
        await bridge.stop()
        await asyncio.wait_for(task, timeout=3.0)

    asyncio.run(_run_bridge())

    assert media.started == 1
    assert media.stopped == 1
    assert events == []
    assert transcriber.calls == []
