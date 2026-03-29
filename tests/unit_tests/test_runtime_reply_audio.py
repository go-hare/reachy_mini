"""Tests for optional resident-runtime reply audio synthesis and playback."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from reachy_mini.runtime.config import SpeechRuntimeConfig
from reachy_mini.runtime.reply_audio import (
    MacOSSayReplySpeechSynthesizer,
    OpenAIReplySpeechSynthesizer,
    ReplyAudioPlayer,
    RuntimeReplyAudioService,
    build_runtime_reply_audio_service,
)


class FakeSpeechDriver:
    """Collect speech-motion callbacks during reply playback."""

    def __init__(self) -> None:
        self.audio_deltas: list[str] = []
        self.reset_calls = 0
        self.speech_active = False

    def feed_audio_delta(self, delta_b64: str) -> bool:
        self.audio_deltas.append(delta_b64)
        self.speech_active = True
        return True

    def reset_speech_motion(self) -> bool:
        self.reset_calls += 1
        self.speech_active = False
        return True


class FakeMedia:
    """Collect audio playback calls without touching real hardware."""

    def __init__(self, sample_rate_hz: int = 24_000) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.started = 0
        self.stopped = 0
        self.samples: list[np.ndarray] = []

    def start_playing(self) -> None:
        self.started += 1

    def stop_playing(self) -> None:
        self.stopped += 1

    def push_audio_sample(self, data: np.ndarray) -> None:
        self.samples.append(np.asarray(data, dtype=np.float32))

    def get_output_audio_samplerate(self) -> int:
        return self.sample_rate_hz


class FakeBinaryResponse:
    """Small OpenAI SDK response stub."""

    def __init__(self, content: bytes) -> None:
        self._content = content

    def read(self) -> bytes:
        return self._content


class FakeStreamingResponse:
    """Small OpenAI streaming response stub."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.closed = False

    async def iter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def close(self) -> None:
        self.closed = True


class FakeStreamingContextManager:
    """Small async context manager matching the OpenAI SDK wrapper."""

    def __init__(self, response: FakeStreamingResponse) -> None:
        self.response = response

    async def __aenter__(self) -> FakeStreamingResponse:
        return self.response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.response.close()


def test_openai_reply_speech_synthesizer_reads_pcm_bytes() -> None:
    """OpenAI synthesizer should request PCM audio and return the response bytes."""

    class FakeSpeechApi:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def create(self, **kwargs: object) -> FakeBinaryResponse:
            self.calls.append(dict(kwargs))
            return FakeBinaryResponse(b"pcm-demo")

    fake_speech_api = FakeSpeechApi()
    fake_client = SimpleNamespace(audio=SimpleNamespace(speech=fake_speech_api))
    synthesizer = OpenAIReplySpeechSynthesizer(
        model="gpt-4o-mini-tts",
        voice="alloy",
        api_key="demo-key",
        instructions="Speak warmly.",
    )

    with patch(
        "reachy_mini.runtime.reply_audio.AsyncOpenAI",
        return_value=fake_client,
    ):
        result = asyncio.run(synthesizer.synthesize_pcm16("你好"))

    assert result == b"pcm-demo"
    assert fake_speech_api.calls == [
        {
            "input": "你好",
            "model": "gpt-4o-mini-tts",
            "voice": "alloy",
            "response_format": "pcm",
            "speed": 1.0,
            "instructions": "Speak warmly.",
        }
    ]


def test_openai_reply_speech_synthesizer_streams_pcm_bytes() -> None:
    """OpenAI synthesizer should stream PCM audio through the SDK context manager."""

    class FakeStreamingSpeechApi:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.response = FakeStreamingResponse([b"ab", b"cd"])

        def create(self, **kwargs: object) -> FakeStreamingContextManager:
            self.calls.append(dict(kwargs))
            return FakeStreamingContextManager(self.response)

    fake_streaming_api = FakeStreamingSpeechApi()
    fake_client = SimpleNamespace(
        audio=SimpleNamespace(
            speech=SimpleNamespace(),
        )
    )
    fake_client.audio.speech.with_streaming_response = fake_streaming_api

    synthesizer = OpenAIReplySpeechSynthesizer(
        model="gpt-4o-mini-tts",
        voice="alloy",
        api_key="demo-key",
    )

    async def _collect() -> list[bytes]:
        with patch(
            "reachy_mini.runtime.reply_audio.AsyncOpenAI",
            return_value=fake_client,
        ):
            return [chunk async for chunk in synthesizer.stream_pcm16("你好")]

    chunks = asyncio.run(_collect())

    assert chunks == [b"ab", b"cd"]
    assert fake_streaming_api.calls == [
        {
            "input": "你好",
            "model": "gpt-4o-mini-tts",
            "voice": "alloy",
            "response_format": "pcm",
            "speed": 1.0,
            "stream_format": "audio",
        }
    ]
    assert fake_streaming_api.response.closed is True


def test_build_runtime_reply_audio_service_supports_macos_say() -> None:
    """Local macOS TTS provider should build without requiring an API key."""

    service = build_runtime_reply_audio_service(
        config=SpeechRuntimeConfig(
            enabled=True,
            provider="macos_say",
            voice="Tingting",
            speed=1.1,
        ),
        media=FakeMedia(),
        speech_driver=FakeSpeechDriver(),
    )

    assert service is not None
    assert isinstance(service.synthesizer, MacOSSayReplySpeechSynthesizer)


def test_reply_audio_player_pushes_audio_and_feeds_speech_motion() -> None:
    """Reply audio playback should drive both the speaker path and speech motion."""
    pcm = np.array([0, 500, -500, 1000], dtype=np.int16).tobytes()
    media = FakeMedia(sample_rate_hz=24_000)
    speech_driver = FakeSpeechDriver()
    player = ReplyAudioPlayer(
        media=media,
        speech_driver=speech_driver,
        chunk_ms=20,
    )

    assert asyncio.run(player.play_pcm16(pcm)) is True
    assert media.started == 1
    assert media.stopped == 1
    assert len(media.samples) == 1
    assert media.samples[0].dtype == np.float32
    assert speech_driver.audio_deltas
    assert speech_driver.reset_calls == 1


def test_reply_audio_player_streams_pcm_and_handles_partial_sample_boundaries() -> None:
    """Streaming playback should tolerate odd byte boundaries and still emit samples."""
    samples = np.arange(0, 960, dtype=np.int16)
    pcm = samples.tobytes()
    media = FakeMedia(sample_rate_hz=24_000)
    speech_driver = FakeSpeechDriver()
    player = ReplyAudioPlayer(
        media=media,
        speech_driver=speech_driver,
        chunk_ms=20,
    )

    async def _stream():
        yield pcm[:481]
        yield pcm[481:1500]
        yield pcm[1500:]

    assert asyncio.run(player.play_pcm16_stream(_stream())) is True
    assert media.started == 1
    assert media.stopped == 1
    assert len(media.samples) >= 1
    assert speech_driver.audio_deltas
    assert speech_driver.reset_calls == 1


def test_reply_audio_player_emits_lifecycle_callbacks() -> None:
    """Reply audio playback should expose started/delta/finished callbacks."""

    pcm = np.array([0, 500, -500, 1000], dtype=np.int16).tobytes()
    media = FakeMedia(sample_rate_hz=24_000)
    speech_driver = FakeSpeechDriver()
    player = ReplyAudioPlayer(
        media=media,
        speech_driver=speech_driver,
        chunk_ms=20,
    )
    started = 0
    deltas: list[str] = []
    finished: list[bool] = []

    async def _play() -> bool:
        nonlocal started

        async def _on_started() -> None:
            nonlocal started
            started += 1

        async def _on_audio_delta(delta_b64: str) -> None:
            deltas.append(delta_b64)

        async def _on_finished(played_any: bool) -> None:
            finished.append(played_any)

        return await player.play_pcm16(
            pcm,
            on_started=_on_started,
            on_audio_delta=_on_audio_delta,
            on_finished=_on_finished,
        )

    assert asyncio.run(_play()) is True
    assert started == 1
    assert deltas
    assert finished == [True]


def test_runtime_reply_audio_service_uses_fallback_api_key() -> None:
    """Reply audio service should reuse the front API key when speech key is omitted."""
    config = SpeechRuntimeConfig(
        enabled=True,
        provider="openai",
        model="gpt-4o-mini-tts",
        voice="alloy",
        chunk_ms=90,
    )

    service = build_runtime_reply_audio_service(
        config=config,
        media=FakeMedia(),
        speech_driver=FakeSpeechDriver(),
        fallback_api_key="front-key",
    )

    assert isinstance(service, RuntimeReplyAudioService)
    assert isinstance(service.synthesizer, OpenAIReplySpeechSynthesizer)
    assert service.synthesizer.api_key == "front-key"
    assert service.player.chunk_ms == 90


def test_runtime_reply_audio_service_prefers_streaming_synthesis() -> None:
    """Reply audio service should prefer streaming synthesis when the backend supports it."""

    class StreamingSynthesizer:
        def __init__(self) -> None:
            self.stream_calls: list[str] = []

        async def synthesize_pcm16(self, text: str) -> bytes:
            raise AssertionError("buffered synthesis should not be used")

        async def stream_pcm16(self, text: str):
            self.stream_calls.append(text)
            yield np.array([1, -1, 2, -2], dtype=np.int16).tobytes()

    synthesizer = StreamingSynthesizer()
    media = FakeMedia()
    speech_driver = FakeSpeechDriver()
    service = RuntimeReplyAudioService(
        synthesizer=synthesizer,
        player=ReplyAudioPlayer(media=media, speech_driver=speech_driver),
    )

    assert asyncio.run(service.speak_text("继续说")) is True
    assert synthesizer.stream_calls == ["继续说"]
    assert media.samples


def test_runtime_reply_audio_service_can_interrupt_active_playback() -> None:
    """Active reply playback should stop promptly when interruption is requested."""

    class StreamingSynthesizer:
        async def synthesize_pcm16(self, text: str) -> bytes:
            raise AssertionError("buffered synthesis should not be used")

        async def stream_pcm16(self, text: str):
            _ = text
            yield np.arange(0, 480, dtype=np.int16).tobytes()
            await asyncio.sleep(0)
            yield np.arange(480, 960, dtype=np.int16).tobytes()

    media = FakeMedia()
    speech_driver = FakeSpeechDriver()
    service = RuntimeReplyAudioService(
        synthesizer=StreamingSynthesizer(),
        player=ReplyAudioPlayer(media=media, speech_driver=speech_driver, chunk_ms=20),
    )
    finished: list[bool] = []

    async def _exercise() -> tuple[bool, bool]:
        task = asyncio.create_task(
            service.speak_text(
                "先播一点",
                on_finished=lambda played_any: finished.append(played_any),
            )
        )
        while media.started == 0:
            await asyncio.sleep(0)
        interrupted = await service.interrupt_playback()
        result = await task
        return interrupted, result

    interrupted, result = asyncio.run(_exercise())

    assert interrupted is True
    assert result is True
    assert media.started == 1
    assert media.stopped == 1
    assert len(media.samples) == 1
    assert finished == [False]
