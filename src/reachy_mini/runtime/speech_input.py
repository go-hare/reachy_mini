"""Robot microphone capture and transcription helpers for resident runtimes."""

from __future__ import annotations

import asyncio
import importlib
import io
import logging
import math
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray
from openai import AsyncOpenAI

from reachy_mini.runtime.config import SpeechInputRuntimeConfig

SpeechLifecycleCallback = Callable[[str], Awaitable[None] | None]
SpeechInputBlockedCallback = Callable[[], bool]


async def _run_callback(result: Awaitable[None] | None) -> None:
    """Await callback results only when they are genuinely awaitable."""

    if isawaitable(result):
        await result


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


def _pcm16_to_wav_bytes(pcm16: bytes, sample_rate_hz: int) -> bytes:
    """Wrap PCM16 mono samples into a small WAV container."""

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(max(int(sample_rate_hz), 1))
        wav_file.writeframes(pcm16)
    return buffer.getvalue()


def _resample_linear(
    samples: NDArray[np.float32],
    source_rate_hz: int,
    target_rate_hz: int,
) -> NDArray[np.float32]:
    """Use lightweight linear resampling for short microphone buffers."""

    if (
        samples.size == 0
        or source_rate_hz <= 0
        or target_rate_hz <= 0
        or source_rate_hz == target_rate_hz
    ):
        return np.asarray(samples, dtype=np.float32)

    target_size = max(1, int(round(samples.size * target_rate_hz / source_rate_hz)))
    source_axis = np.linspace(0.0, 1.0, num=samples.size, dtype=np.float32, endpoint=True)
    target_axis = np.linspace(0.0, 1.0, num=target_size, dtype=np.float32, endpoint=True)
    return np.interp(target_axis, source_axis, samples).astype(np.float32, copy=False)


def _rms_dbfs(samples: NDArray[np.float32]) -> float:
    """Compute RMS loudness in dBFS for mono float32 audio."""

    audio = np.asarray(samples, dtype=np.float32)
    if audio.size == 0:
        return -120.0
    rms = np.sqrt(np.mean(audio * audio, dtype=np.float32) + 1e-12, dtype=np.float32)
    return float(20.0 * math.log10(float(rms) + 1e-12))


class SpeechInputTranscriber(Protocol):
    """Protocol for speech-to-text backends used by the robot microphone bridge."""

    async def transcribe_pcm16(self, pcm16: bytes, sample_rate_hz: int) -> str:
        """Return one final transcript for a single captured utterance."""


@dataclass(slots=True)
class OpenAISpeechInputTranscriber:
    """Thin OpenAI transcription adapter for final utterance decoding."""

    model: str
    api_key: str
    base_url: str = ""
    language: str = ""
    _client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is not None:
            return self._client

        kwargs: dict[str, Any] = {"api_key": self.api_key}
        if str(self.base_url or "").strip():
            kwargs["base_url"] = str(self.base_url).strip()
        self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def transcribe_pcm16(self, pcm16: bytes, sample_rate_hz: int) -> str:
        """Send one WAV-wrapped utterance to the configured transcription endpoint."""

        if not pcm16:
            return ""

        kwargs: dict[str, Any] = {
            "model": str(self.model).strip(),
            "file": ("speech.wav", _pcm16_to_wav_bytes(pcm16, sample_rate_hz), "audio/wav"),
            "response_format": "text",
        }
        if str(self.language or "").strip():
            kwargs["language"] = str(self.language).strip()

        response = await self._get_client().audio.transcriptions.create(**kwargs)
        if isinstance(response, str):
            return response.strip()
        text = getattr(response, "text", None)
        if isinstance(text, str):
            return text.strip()
        return str(response or "").strip()


@dataclass(slots=True)
class MLXWhisperSpeechInputTranscriber:
    """Local MLX Whisper transcription backend for macOS speech input."""

    model: str = "mlx-community/whisper-small-mlx"
    language: str = ""

    async def transcribe_pcm16(self, pcm16: bytes, sample_rate_hz: int) -> str:
        """Transcribe one utterance by delegating to ``mlx_whisper`` in a worker thread."""

        if not pcm16:
            return ""

        module = importlib.import_module("mlx_whisper")

        def _transcribe_once() -> str:
            audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
            if sample_rate_hz != 16_000:
                audio = _resample_linear(audio, sample_rate_hz, 16_000)
            kwargs: dict[str, Any] = {
                "path_or_hf_repo": str(self.model).strip(),
                "verbose": False,
                "temperature": 0.0,
                "condition_on_previous_text": False,
                "no_speech_threshold": 0.8,
            }
            if str(self.language or "").strip():
                kwargs["language"] = str(self.language).strip()
            result = module.transcribe(audio, **kwargs)

            if isinstance(result, dict):
                return str(result.get("text", "") or "").strip()
            return str(result or "").strip()

        return await asyncio.to_thread(_transcribe_once)


def build_runtime_speech_input_transcriber(
    *,
    config: SpeechInputRuntimeConfig,
    fallback_api_key: str = "",
    fallback_base_url: str = "",
) -> SpeechInputTranscriber | None:
    """Build the optional speech-input transcriber for one resident runtime."""

    if not config.enabled:
        return None

    provider = str(config.provider or "").strip().lower()
    if not provider:
        raise RuntimeError("Speech input requires a configured `provider`.")

    if provider == "openai":
        api_key = str(config.api_key or "").strip() or str(fallback_api_key or "").strip()
        if not api_key:
            raise RuntimeError("Speech input provider 'openai' requires `api_key`.")
        return OpenAISpeechInputTranscriber(
            model=str(config.model or "").strip()
            or SpeechInputRuntimeConfig().model,
            api_key=api_key,
            base_url=str(config.base_url or "").strip()
            or str(fallback_base_url or "").strip(),
            language=str(config.language or "").strip(),
        )

    if provider in {"mlx_whisper", "mlx-whisper"}:
        model = str(config.model or "").strip()
        if not model or model == SpeechInputRuntimeConfig().model:
            model = "mlx-community/whisper-small-mlx"
        return MLXWhisperSpeechInputTranscriber(
            model=model,
            language=str(config.language or "").strip(),
        )

    raise RuntimeError(f"Unsupported speech input provider: {config.provider or '<empty>'}")


@dataclass(slots=True)
class RuntimeMicrophoneBridge:
    """Capture robot microphone audio, segment utterances, and submit transcripts."""

    media: Any
    transcriber: SpeechInputTranscriber
    config: SpeechInputRuntimeConfig
    logger: logging.Logger
    on_speech_started: SpeechLifecycleCallback
    on_speech_stopped: SpeechLifecycleCallback
    on_user_text: SpeechLifecycleCallback
    input_blocked: SpeechInputBlockedCallback | None = None
    poll_interval_s: float = 0.01
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _transcription_tasks: set[asyncio.Task[None]] = field(default_factory=set, init=False, repr=False)
    _user_speaking: bool = field(default=False, init=False, repr=False)
    _active_chunks: list[NDArray[np.float32]] = field(default_factory=list, init=False, repr=False)
    _active_sample_rate_hz: int = field(default=16_000, init=False, repr=False)
    _active_duration_ms: int = field(default=0, init=False, repr=False)
    _attack_ms: int = field(default=0, init=False, repr=False)
    _release_ms: int = field(default=0, init=False, repr=False)

    async def run(self) -> None:
        """Start recording, segment voice turns, and forward final transcripts."""

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
                if self._input_is_blocked():
                    self._reset_pending_capture()
                    await asyncio.sleep(self.poll_interval_s)
                    continue
                await self._consume_audio_frame(audio_frame)
        finally:
            if self._user_speaking:
                await self._finalize_current_utterance()
            for task in list(self._transcription_tasks):
                try:
                    await task
                except Exception as exc:
                    self.logger.warning("Speech transcription task failed: %s", exc)
            self._transcription_tasks.clear()
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
            for attribute in ("start_recording", "stop_recording", "get_audio_sample", "get_input_audio_samplerate")
        )

    def _input_is_blocked(self) -> bool:
        callback = self.input_blocked
        if callback is None:
            return False
        try:
            return bool(callback())
        except Exception as exc:
            self.logger.warning("Speech input block check failed: %s", exc)
            return False

    def _reset_pending_capture(self) -> None:
        self._user_speaking = False
        self._active_chunks = []
        self._active_duration_ms = 0
        self._attack_ms = 0
        self._release_ms = 0

    async def _consume_audio_frame(self, audio_frame: NDArray[Any]) -> None:
        sample_rate_hz = int(self.media.get_input_audio_samplerate())
        samples = _to_float32_mono(audio_frame)
        if samples.size == 0 or sample_rate_hz <= 0:
            return

        frame_duration_ms = max(1, int(round(samples.shape[0] * 1000 / sample_rate_hz)))
        loudness_db = _rms_dbfs(samples)

        if self._user_speaking:
            self._active_chunks.append(samples)
            self._active_duration_ms += frame_duration_ms

            if loudness_db <= float(self.config.vad_db_off):
                self._release_ms += frame_duration_ms
            else:
                self._release_ms = 0

            if (
                self._release_ms >= int(self.config.vad_release_ms)
                or self._active_duration_ms >= int(self.config.max_utterance_ms)
            ):
                await self._finalize_current_utterance()
            return

        if loudness_db >= float(self.config.vad_db_on):
            self._attack_ms += frame_duration_ms
        else:
            self._attack_ms = 0

        if self._attack_ms < int(self.config.vad_attack_ms):
            return

        self._user_speaking = True
        self._active_chunks = [samples]
        self._active_sample_rate_hz = sample_rate_hz
        self._active_duration_ms = frame_duration_ms
        self._attack_ms = 0
        self._release_ms = 0
        await _run_callback(self.on_speech_started(""))

    async def _finalize_current_utterance(self) -> None:
        chunks = list(self._active_chunks)
        sample_rate_hz = int(self._active_sample_rate_hz)
        duration_ms = int(self._active_duration_ms)

        self._user_speaking = False
        self._active_chunks = []
        self._active_duration_ms = 0
        self._attack_ms = 0
        self._release_ms = 0

        await _run_callback(self.on_speech_stopped(""))

        if not chunks or duration_ms < int(self.config.min_utterance_ms):
            return

        audio = np.concatenate(chunks).astype(np.float32, copy=False)
        if _rms_dbfs(audio) <= float(self.config.vad_db_off):
            return
        pcm16 = _float32_to_pcm16(audio)
        task = asyncio.create_task(
            self._transcribe_and_submit(pcm16, sample_rate_hz),
            name="runtime-microphone-transcription",
        )
        self._transcription_tasks.add(task)
        task.add_done_callback(self._transcription_tasks.discard)

    async def _transcribe_and_submit(self, pcm16: bytes, sample_rate_hz: int) -> None:
        try:
            transcript = (
                await self.transcriber.transcribe_pcm16(pcm16, sample_rate_hz)
            ).strip()
        except Exception as exc:
            self.logger.warning("Runtime microphone transcription failed: %s", exc)
            return

        if not transcript:
            return

        try:
            await _run_callback(self.on_user_text(transcript))
        except Exception as exc:
            self.logger.warning("Runtime microphone transcript delivery failed: %s", exc)


__all__ = [
    "MLXWhisperSpeechInputTranscriber",
    "OpenAISpeechInputTranscriber",
    "RuntimeMicrophoneBridge",
    "SpeechInputTranscriber",
    "build_runtime_speech_input_transcriber",
]
