"""Speech-session capture and streaming transcription helpers for resident runtimes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Any, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray
import websockets

from reachy_mini.runtime.config import SpeechInputRuntimeConfig

SpeechLifecycleCallback = Callable[[str], Awaitable[None] | None]
SpeechInputBlockedCallback = Callable[[], bool]


async def _run_callback(result: Awaitable[None] | None) -> None:
    """Await callback results only when they are genuinely awaitable."""

    if isawaitable(result):
        await result


def _noop_speech_callback(_: str) -> None:
    """Default no-op callback used when partial transcripts are ignored."""


def _normalize_websocket_url(url: str) -> str:
    """Accept ws/wss/http/https endpoints and normalize them for websocket clients."""

    resolved = str(url or "").strip()
    if not resolved:
        return ""
    if resolved.startswith("http://"):
        return f"ws://{resolved[7:]}"
    if resolved.startswith("https://"):
        return f"wss://{resolved[8:]}"
    if resolved.startswith(("ws://", "wss://")):
        return resolved
    return f"ws://{resolved}"


def _to_float32_mono(audio_frame: NDArray[Any]) -> NDArray[np.float32]:
    """Convert arbitrary input chunks into mono float32 samples in [-1, 1]."""

    samples = np.asarray(audio_frame)
    if samples.ndim == 0:
        return np.zeros(0, dtype=np.float32)

    if samples.ndim == 2:
        if samples.shape[1] > samples.shape[0]:
            samples = samples.T
        if samples.shape[1] > 1:
            samples = samples[:, 0]
        else:
            samples = samples.reshape(-1)
    elif samples.ndim > 2:
        samples = samples.reshape(-1)

    if np.issubdtype(samples.dtype, np.floating):
        return np.asarray(samples, dtype=np.float32)

    info = np.iinfo(samples.dtype)
    scale = float(max(-info.min, info.max)) or 1.0
    return np.asarray(samples, dtype=np.float32) / scale


def _float32_to_pcm16(audio_frame: NDArray[np.float32]) -> bytes:
    """Convert float32 mono samples in [-1, 1] into PCM16 bytes."""

    clipped = np.clip(np.asarray(audio_frame, dtype=np.float32), -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()


class SpeechInputStreamSession(Protocol):
    """One streaming speech session that accepts PCM16 chunks and yields text."""

    final_text: str

    async def push_pcm16(self, pcm16: bytes) -> None:
        """Send one PCM16 chunk to the active streaming session."""

    async def finish(self) -> None:
        """Finalize the active stream and wait for any final transcript."""

    async def close(self) -> None:
        """Close the active streaming session without requiring a final transcript."""


@runtime_checkable
class SpeechInputStreamProvider(Protocol):
    """Streaming speech provider contract used by the runtime speech session."""

    async def start_streaming_session(
        self,
        *,
        sample_rate_hz: int,
        on_speech_started: SpeechLifecycleCallback,
        on_speech_stopped: SpeechLifecycleCallback,
        on_partial: SpeechLifecycleCallback,
        on_final: SpeechLifecycleCallback,
        source_name: str,
    ) -> SpeechInputStreamSession:
        """Open one streaming session and return the live stream handle."""


@dataclass(slots=True)
class FunASRWebSocketSpeechStream:
    """One FunASR websocket session that emits partial and final transcripts."""

    websocket: Any
    logger: logging.Logger
    on_speech_started: SpeechLifecycleCallback
    on_speech_stopped: SpeechLifecycleCallback
    on_partial: SpeechLifecycleCallback
    on_final: SpeechLifecycleCallback
    source_name: str = "runtime-speech"
    finish_timeout_s: float = 6.0
    final_text: str = field(default="", init=False)
    _receiver_task: asyncio.Task[None] = field(init=False, repr=False)
    _final_event: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _stop_sent: bool = field(default=False, init=False, repr=False)
    _latest_text: str = field(default="", init=False, repr=False)
    _awaiting_final: bool = field(default=False, init=False, repr=False)
    _speech_active: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self._receiver_task = asyncio.create_task(
            self._receive_loop(),
            name=f"{self.source_name}-funasr-recv",
        )

    async def push_pcm16(self, pcm16: bytes) -> None:
        """Send one PCM16 chunk to FunASR."""

        if self._closed or not pcm16:
            return
        await self.websocket.send(bytes(pcm16))

    async def finish(self) -> None:
        """Signal end-of-speech and wait briefly for the final transcript."""

        if self._closed:
            return
        if not self._stop_sent:
            self._stop_sent = True
            await self.websocket.send(json.dumps({"is_speaking": False}, ensure_ascii=False))
        if self._awaiting_final:
            try:
                await asyncio.wait_for(
                    self._final_event.wait(),
                    timeout=max(self.finish_timeout_s, 0.5),
                )
            except asyncio.TimeoutError:
                if self._latest_text:
                    self.logger.warning(
                        "%s FunASR final transcript timed out; falling back to latest partial.",
                        self.source_name,
                    )
                    await self._emit_final(self._latest_text)
                else:
                    await self._emit_speech_stopped()
                    self._awaiting_final = False
                    self._final_event.set()
        await self.close()

    async def close(self) -> None:
        """Close the websocket stream and stop its receiver task."""

        current_task = asyncio.current_task()
        if self._speech_active:
            with contextlib.suppress(Exception):
                await self._emit_speech_stopped()
        self._awaiting_final = False
        self._final_event.set()
        if not self._closed:
            self._closed = True
            with contextlib.suppress(Exception):
                await self.websocket.close()
        if self._receiver_task.done():
            if current_task is not self._receiver_task:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._receiver_task
            return
        self._receiver_task.cancel()
        if current_task is not self._receiver_task:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._receiver_task

    async def _receive_loop(self) -> None:
        online_text = ""
        offline_text = ""
        try:
            while True:
                message = await self.websocket.recv()
                if isinstance(message, bytes):
                    message = message.decode("utf-8", errors="ignore")
                if not isinstance(message, str) or not message.strip():
                    continue

                payload = json.loads(message)
                text = str(payload.get("text", "") or "")
                mode = str(payload.get("mode", "") or "").strip().lower()
                is_final = bool(payload.get("is_final", False))

                if mode in {"online", "2pass-online"}:
                    online_text += text
                    combined_text = online_text if mode == "online" else f"{offline_text}{online_text}"
                    await self._emit_partial(combined_text)
                    if is_final and combined_text:
                        await self._emit_final(combined_text)
                        online_text = ""
                        offline_text = ""
                    continue

                if mode in {"offline", "2pass-offline"}:
                    online_text = ""
                    offline_text += text
                    await self._emit_partial(offline_text)
                    if (is_final or mode == "offline") and offline_text:
                        await self._emit_final(offline_text)
                        online_text = ""
                        offline_text = ""
                    continue

                fallback_text = text or offline_text or online_text
                if fallback_text:
                    await self._emit_partial(fallback_text)
                if is_final and fallback_text:
                    await self._emit_final(fallback_text)
                    online_text = ""
                    offline_text = ""
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closed:
                self.logger.warning("%s FunASR receive loop failed: %s", self.source_name, exc)

    async def _emit_speech_started(self) -> None:
        if self._speech_active:
            return
        self._speech_active = True
        self._awaiting_final = True
        self._latest_text = ""
        self._final_event.clear()
        await _run_callback(self.on_speech_started(""))

    async def _emit_speech_stopped(self) -> None:
        if not self._speech_active:
            return
        self._speech_active = False
        await _run_callback(self.on_speech_stopped(""))

    async def _emit_partial(self, text: str) -> None:
        normalized = str(text or "").strip()
        if not normalized:
            return
        if not self._speech_active:
            await self._emit_speech_started()
        if normalized == self._latest_text:
            return
        self._latest_text = normalized
        await _run_callback(self.on_partial(normalized))

    async def _emit_final(self, text: str) -> None:
        normalized = str(text or "").strip() or self._latest_text
        self.final_text = normalized
        if normalized and not self._speech_active:
            await self._emit_speech_started()
        if self._speech_active:
            await self._emit_speech_stopped()
        self._awaiting_final = False
        if normalized:
            self._latest_text = normalized
            await _run_callback(self.on_final(normalized))
        self._final_event.set()


@dataclass(slots=True)
class FunASRWebSocketSpeechInputProvider:
    """Thin FunASR websocket client that can stream partial transcripts."""

    base_url: str
    mode: str = "2pass"
    language: str = ""
    chunk_size: tuple[int, int, int] = (5, 10, 5)
    chunk_interval: int = 10
    encoder_chunk_look_back: int = 4
    decoder_chunk_look_back: int = 0
    finish_timeout_s: float = 6.0
    itn: bool = True
    logger: logging.Logger | None = None

    async def start_streaming_session(
        self,
        *,
        sample_rate_hz: int,
        on_speech_started: SpeechLifecycleCallback,
        on_speech_stopped: SpeechLifecycleCallback,
        on_partial: SpeechLifecycleCallback,
        on_final: SpeechLifecycleCallback,
        source_name: str,
    ) -> SpeechInputStreamSession:
        """Open one FunASR websocket stream for the active utterance."""

        resolved_url = _normalize_websocket_url(self.base_url)
        if not resolved_url:
            raise RuntimeError("Speech input provider 'funasr' requires `base_url`.")
        websocket = await websockets.connect(
            resolved_url,
            subprotocols=["binary"],
            ping_interval=None,
        )
        init_payload = {
            "mode": str(self.mode or "2pass").strip() or "2pass",
            "chunk_size": list(self.chunk_size),
            "chunk_interval": int(self.chunk_interval),
            "encoder_chunk_look_back": int(self.encoder_chunk_look_back),
            "decoder_chunk_look_back": int(self.decoder_chunk_look_back),
            "wav_name": f"{source_name}-{time.time_ns()}",
            "is_speaking": True,
            "audio_fs": max(int(sample_rate_hz), 1),
            "itn": bool(self.itn),
        }
        if str(self.language or "").strip():
            init_payload["language"] = str(self.language).strip()
        try:
            await websocket.send(json.dumps(init_payload, ensure_ascii=False))
        except Exception:
            with contextlib.suppress(Exception):
                await websocket.close()
            raise
        return FunASRWebSocketSpeechStream(
            websocket=websocket,
            logger=self.logger or logging.getLogger(__name__),
            on_speech_started=on_speech_started,
            on_speech_stopped=on_speech_stopped,
            on_partial=on_partial,
            on_final=on_final,
            source_name=source_name,
            finish_timeout_s=self.finish_timeout_s,
        )


def build_runtime_speech_session_provider(
    *,
    config: SpeechInputRuntimeConfig,
) -> SpeechInputStreamProvider | None:
    """Build the optional streaming speech provider for one resident runtime."""

    if not config.enabled:
        return None

    provider = str(config.provider or "").strip().lower()
    if not provider:
        raise RuntimeError("Speech input requires a configured `provider`.")
    if provider not in {"funasr", "fun_asr"}:
        raise RuntimeError(
            f"Unsupported streaming speech input provider: {config.provider or '<empty>'}"
        )

    mode = str(config.model or "").strip().lower() or "2pass"
    return FunASRWebSocketSpeechInputProvider(
        base_url=str(config.base_url or "").strip(),
        mode=mode,
        language=str(config.language or "").strip(),
        chunk_size=tuple(int(value) for value in config.stream_chunk_size),
        chunk_interval=max(int(config.stream_chunk_interval), 1),
        encoder_chunk_look_back=max(int(config.stream_encoder_chunk_look_back), 0),
        decoder_chunk_look_back=max(int(config.stream_decoder_chunk_look_back), 0),
        finish_timeout_s=max(float(config.stream_finish_timeout_s), 0.5),
        itn=bool(config.stream_itn),
        logger=logging.getLogger(__name__),
    )


@dataclass(slots=True)
class RuntimeSpeechSession:
    """Continuously stream audio from one source and relay provider speech events."""

    provider: SpeechInputStreamProvider
    config: SpeechInputRuntimeConfig
    logger: logging.Logger
    on_speech_started: SpeechLifecycleCallback
    on_speech_stopped: SpeechLifecycleCallback
    on_user_text: SpeechLifecycleCallback
    on_user_text_partial: SpeechLifecycleCallback = _noop_speech_callback
    input_blocked: SpeechInputBlockedCallback | None = None
    source_name: str = "runtime-speech"
    _stream_session: SpeechInputStreamSession | None = field(default=None, init=False, repr=False)
    _stream_sample_rate_hz: int = field(default=0, init=False, repr=False)
    _next_start_attempt_at: float = field(default=0.0, init=False, repr=False)
    _last_partial_transcript: str = field(default="", init=False, repr=False)

    async def feed_audio_frame(
        self,
        audio_frame: NDArray[Any],
        sample_rate_hz: int,
    ) -> None:
        """Feed one audio frame into the continuous provider-owned streaming session."""

        if self._input_is_blocked():
            await self._stop_stream(flush=False)
            return

        samples = _to_float32_mono(audio_frame)
        resolved_sample_rate_hz = int(sample_rate_hz)
        if samples.size == 0 or resolved_sample_rate_hz <= 0:
            return

        if (
            self._stream_session is not None
            and self._stream_sample_rate_hz > 0
            and self._stream_sample_rate_hz != resolved_sample_rate_hz
        ):
            await self._stop_stream(flush=False)

        await self._ensure_stream(sample_rate_hz=resolved_sample_rate_hz)
        if self._stream_session is None:
            return
        await self._push_stream_chunk(samples)

    async def finish_capture(self) -> None:
        """Finalize any pending provider-side utterance after the input source stops."""

        await self._stop_stream(flush=True)

    async def close(self, *, flush: bool = True) -> None:
        """Stop the session and optionally flush the provider-owned stream."""

        await self._stop_stream(flush=flush)

    def _input_is_blocked(self) -> bool:
        callback = self.input_blocked
        if callback is None:
            return False
        try:
            return bool(callback())
        except Exception as exc:
            self.logger.warning("%s input block check failed: %s", self.source_name, exc)
            return False

    def _reset_stream_state(self) -> None:
        self._stream_session = None
        self._stream_sample_rate_hz = 0
        self._last_partial_transcript = ""

    async def _ensure_stream(self, *, sample_rate_hz: int) -> None:
        if self._stream_session is not None:
            return
        now = time.monotonic()
        if now < self._next_start_attempt_at:
            return
        try:
            self._stream_session = await self.provider.start_streaming_session(
                sample_rate_hz=sample_rate_hz,
                on_speech_started=self._handle_speech_started,
                on_speech_stopped=self._handle_speech_stopped,
                on_partial=self._handle_partial_transcript,
                on_final=self._handle_final_transcript,
                source_name=self.source_name,
            )
            self._stream_sample_rate_hz = sample_rate_hz
            self._next_start_attempt_at = 0.0
        except Exception as exc:
            self.logger.warning(
                "%s streaming provider setup failed: %s",
                self.source_name,
                exc,
            )
            self._stream_session = None
            self._stream_sample_rate_hz = 0
            self._next_start_attempt_at = now + 1.0

    async def _push_stream_chunk(self, samples: NDArray[np.float32]) -> None:
        session = self._stream_session
        if session is None:
            return
        pcm16 = _float32_to_pcm16(samples)
        if not pcm16:
            return
        try:
            await session.push_pcm16(pcm16)
        except Exception as exc:
            self.logger.warning("%s stream push failed: %s", self.source_name, exc)
            self._reset_stream_state()
            self._next_start_attempt_at = time.monotonic() + 1.0
            with contextlib.suppress(Exception):
                await session.close()

    async def _handle_speech_started(self, _: str) -> None:
        self._last_partial_transcript = ""
        await _run_callback(self.on_speech_started(""))

    async def _handle_speech_stopped(self, _: str) -> None:
        await _run_callback(self.on_speech_stopped(""))

    async def _stop_stream(self, *, flush: bool) -> None:
        session = self._stream_session
        self._reset_stream_state()
        if session is None:
            return
        try:
            if flush:
                await session.finish()
            else:
                await session.close()
        except Exception as exc:
            self.logger.warning("%s streaming shutdown failed: %s", self.source_name, exc)
        finally:
            with contextlib.suppress(Exception):
                await session.close()

    async def _handle_partial_transcript(self, transcript: str) -> None:
        normalized = str(transcript or "").strip()
        if not normalized or normalized == self._last_partial_transcript:
            return
        self._last_partial_transcript = normalized
        try:
            await _run_callback(self.on_user_text_partial(normalized))
        except Exception as exc:
            self.logger.warning("%s partial transcript delivery failed: %s", self.source_name, exc)

    async def _handle_final_transcript(self, transcript: str) -> None:
        normalized = str(transcript or "").strip()
        if not normalized:
            return
        self._last_partial_transcript = normalized
        try:
            await _run_callback(self.on_user_text(normalized))
        except Exception as exc:
            self.logger.warning("%s final transcript delivery failed: %s", self.source_name, exc)


@dataclass(slots=True)
class RuntimeMicrophoneBridge:
    """Capture robot microphone audio, segment utterances, and submit transcripts."""

    media: Any
    provider: SpeechInputStreamProvider
    config: SpeechInputRuntimeConfig
    logger: logging.Logger
    on_speech_started: SpeechLifecycleCallback
    on_speech_stopped: SpeechLifecycleCallback
    on_user_text: SpeechLifecycleCallback
    on_user_text_partial: SpeechLifecycleCallback = _noop_speech_callback
    input_blocked: SpeechInputBlockedCallback | None = None
    poll_interval_s: float = 0.01
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _session: RuntimeSpeechSession = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._session = RuntimeSpeechSession(
            provider=self.provider,
            config=self.config,
            logger=self.logger,
            on_speech_started=self.on_speech_started,
            on_speech_stopped=self.on_speech_stopped,
            on_user_text=self.on_user_text,
            on_user_text_partial=self.on_user_text_partial,
            input_blocked=self.input_blocked,
            source_name="runtime-microphone",
        )

    async def run(self) -> None:
        """Start recording, segment voice turns, and forward transcripts."""

        if not self._has_media_input():
            self.logger.warning("Runtime microphone bridge skipped: media input is unavailable.")
            return

        self._stop_event.clear()
        self.media.start_recording()
        try:
            while not self._stop_event.is_set():
                audio_frame = await asyncio.to_thread(self.media.get_audio_sample)
                if audio_frame is None:
                    await asyncio.sleep(self.poll_interval_s)
                    continue
                sample_rate_hz = int(self.media.get_input_audio_samplerate())
                await self._session.feed_audio_frame(audio_frame, sample_rate_hz)
        finally:
            await self._session.close(flush=True)
            try:
                self.media.stop_recording()
            except Exception as exc:
                self.logger.debug("Failed to stop recording cleanly: %s", exc)

    async def stop(self) -> None:
        """Request bridge shutdown on the next poll tick."""

        self._stop_event.set()
        await asyncio.sleep(0)

    def _has_media_input(self) -> bool:
        return all(
            hasattr(self.media, attribute)
            for attribute in (
                "start_recording",
                "stop_recording",
                "get_audio_sample",
                "get_input_audio_samplerate",
            )
        )


__all__ = [
    "FunASRWebSocketSpeechInputProvider",
    "RuntimeMicrophoneBridge",
    "RuntimeSpeechSession",
    "SpeechInputStreamProvider",
    "SpeechInputStreamSession",
    "build_runtime_speech_session_provider",
]
