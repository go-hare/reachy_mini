"""Tests for robot microphone speech-session helpers."""

import asyncio

import numpy as np

from reachy_mini.runtime.config import SpeechInputRuntimeConfig
from reachy_mini.runtime.speech_session import (
    FunASRWebSocketSpeechInputProvider,
    RuntimeMicrophoneBridge,
    RuntimeSpeechSession,
    build_runtime_speech_session_provider,
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


class FakeStreamSession:
    """Minimal streaming session that drives speech lifecycle from pushed audio."""

    def __init__(
        self,
        *,
        partial_text: str,
        final_text: str,
        on_speech_started,
        on_speech_stopped,
        on_partial,
        on_final,
    ) -> None:
        self.partial_text = partial_text
        self.final_text = final_text
        self._on_speech_started = on_speech_started
        self._on_speech_stopped = on_speech_stopped
        self._on_partial = on_partial
        self._on_final = on_final
        self.pushed_chunks: list[bytes] = []
        self.finished = False
        self.closed = False
        self.started_emitted = False
        self.stopped_emitted = False
        self.final_emitted = False

    async def push_pcm16(self, pcm16: bytes) -> None:
        self.pushed_chunks.append(bytes(pcm16))
        samples = np.frombuffer(pcm16, dtype=np.int16)
        if samples.size == 0:
            return
        speaking = bool(np.max(np.abs(samples)) > 0)
        if speaking:
            if not self.started_emitted:
                self.started_emitted = True
                self.stopped_emitted = False
                self.final_emitted = False
                await self._on_speech_started("")
                await self._on_partial(self.partial_text)
            return
        if self.started_emitted and not self.stopped_emitted:
            self.stopped_emitted = True
            self.final_emitted = True
            await self._on_speech_stopped("")
            await self._on_final(self.final_text)

    async def finish(self) -> None:
        self.finished = True
        if self.started_emitted and not self.stopped_emitted:
            self.stopped_emitted = True
            await self._on_speech_stopped("")
        if self.started_emitted and not self.final_emitted:
            self.final_emitted = True
            await self._on_final(self.final_text)

    async def close(self) -> None:
        self.closed = True
        if self.started_emitted and not self.stopped_emitted:
            self.stopped_emitted = True
            await self._on_speech_stopped("")


class FakeStreamingProvider:
    """Return a deterministic fake stream session for each opened stream."""

    def __init__(self, *, partial_text: str = "你好", final_text: str = "你好，Reachy") -> None:
        self.partial_text = partial_text
        self.final_text = final_text
        self.sessions: list[FakeStreamSession] = []
        self.calls: list[dict[str, object]] = []

    async def start_streaming_session(
        self,
        *,
        sample_rate_hz: int,
        on_speech_started,
        on_speech_stopped,
        on_partial,
        on_final,
        source_name: str,
    ) -> FakeStreamSession:
        self.calls.append(
            {
                "sample_rate_hz": int(sample_rate_hz),
                "source_name": str(source_name),
            }
        )
        session = FakeStreamSession(
            partial_text=self.partial_text,
            final_text=self.final_text,
            on_speech_started=on_speech_started,
            on_speech_stopped=on_speech_stopped,
            on_partial=on_partial,
            on_final=on_final,
        )
        self.sessions.append(session)
        return session


def test_build_runtime_speech_session_provider_supports_funasr() -> None:
    """FunASR speech-session provider should build the matching streaming provider."""

    provider = build_runtime_speech_session_provider(
        config=SpeechInputRuntimeConfig(
            enabled=True,
            provider="funasr",
            base_url="ws://127.0.0.1:10096",
            model="2pass",
        )
    )

    assert isinstance(provider, FunASRWebSocketSpeechInputProvider)


def test_runtime_microphone_bridge_emits_started_partial_stopped_and_user_text() -> None:
    """Robot microphone bridge should relay provider-native lifecycle events."""

    silence = np.zeros(320, dtype=np.float32)
    speech = np.full(320, 0.25, dtype=np.float32)
    frames = [silence] * 2 + [speech] * 6 + [silence] * 40
    media = FakeMedia(frames)
    logger = FakeLogger()
    provider = FakeStreamingProvider(partial_text="你好", final_text="你好，Reachy")
    config = SpeechInputRuntimeConfig(
        enabled=True,
        provider="funasr",
        base_url="ws://127.0.0.1:10096",
        model="2pass",
    )

    events: list[tuple[str, str]] = []
    transcript_ready = asyncio.Event()

    async def on_speech_started(_: str) -> None:
        events.append(("started", ""))

    async def on_speech_stopped(_: str) -> None:
        events.append(("stopped", ""))

    async def on_user_text_partial(text: str) -> None:
        events.append(("partial", text))

    async def on_user_text(text: str) -> None:
        events.append(("user_text", text))
        transcript_ready.set()

    bridge = RuntimeMicrophoneBridge(
        media=media,
        provider=provider,
        config=config,
        logger=logger,  # type: ignore[arg-type]
        on_speech_started=on_speech_started,
        on_speech_stopped=on_speech_stopped,
        on_user_text=on_user_text,
        on_user_text_partial=on_user_text_partial,
    )

    async def _run_bridge() -> None:
        task = asyncio.create_task(bridge.run())
        await asyncio.wait_for(transcript_ready.wait(), timeout=3.0)
        await bridge.stop()
        await asyncio.wait_for(task, timeout=3.0)

    asyncio.run(_run_bridge())

    assert media.started == 1
    assert media.stopped == 1
    assert events[0] == ("started", "")
    assert ("partial", "你好") in events
    assert events[-2] == ("stopped", "")
    assert events[-1] == ("user_text", "你好，Reachy")
    assert len(provider.calls) == 1
    assert provider.calls[0]["sample_rate_hz"] == 16_000
    assert provider.sessions[0].finished is True
    assert provider.sessions[0].pushed_chunks
    assert provider.sessions[0].pushed_chunks[0] == b"\x00\x00" * 320


def test_runtime_microphone_bridge_ignores_input_while_assistant_audio_is_active() -> None:
    """Robot microphone bridge should not start a new capture while assistant audio is active."""

    speech = np.full(320, 0.25, dtype=np.float32)
    media = FakeMedia([speech] * 20)
    logger = FakeLogger()
    provider = FakeStreamingProvider(final_text="这句不该被送出")
    events: list[tuple[str, str]] = []

    bridge = RuntimeMicrophoneBridge(
        media=media,
        provider=provider,
        config=SpeechInputRuntimeConfig(
            enabled=True,
            provider="funasr",
            base_url="ws://127.0.0.1:10096",
            model="2pass",
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
    assert provider.calls == []


def test_runtime_speech_session_emits_stopped_when_blocked_mid_turn() -> None:
    """Provider-driven sessions should still close the speech lifecycle when blocked."""

    provider = FakeStreamingProvider(partial_text="打断", final_text="不应该完成")
    logger = FakeLogger()
    blocked = {"value": False}
    events: list[tuple[str, str]] = []

    async def on_speech_started(_: str) -> None:
        events.append(("started", ""))

    async def on_speech_stopped(_: str) -> None:
        events.append(("stopped", ""))

    session = RuntimeSpeechSession(
        provider=provider,
        config=SpeechInputRuntimeConfig(
            enabled=True,
            provider="funasr",
            base_url="ws://127.0.0.1:10096",
            model="2pass",
        ),
        logger=logger,  # type: ignore[arg-type]
        on_speech_started=on_speech_started,
        on_speech_stopped=on_speech_stopped,
        on_user_text=lambda text: events.append(("user_text", text)),
        on_user_text_partial=lambda text: events.append(("partial", text)),
        input_blocked=lambda: blocked["value"],
    )

    async def _run_session() -> None:
        await session.feed_audio_frame(np.full(320, 0.25, dtype=np.float32), 16_000)
        blocked["value"] = True
        await session.feed_audio_frame(np.zeros(320, dtype=np.float32), 16_000)
        await session.close(flush=False)

    asyncio.run(_run_session())

    assert events[0] == ("started", "")
    assert ("partial", "打断") in events
    assert ("stopped", "") in events
    assert ("user_text", "不应该完成") not in events
