"""Optional reply-audio synthesis and playback helpers for resident runtime apps."""

from __future__ import annotations

import asyncio
import base64
import logging
import shutil
import tempfile
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from inspect import isawaitable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np
from numpy.typing import NDArray
from openai import AsyncOpenAI
from scipy.signal import resample

from reachy_mini.runtime.config import SpeechRuntimeConfig

if TYPE_CHECKING:
    from reachy_mini.runtime.speech_driver import SpeechDriver

PCM_SAMPLE_RATE_HZ = 24_000
ReplyAudioEventCallback = Callable[[], Awaitable[None] | None]
ReplyAudioDeltaCallback = Callable[[str], Awaitable[None] | None]
ReplyAudioFinishedCallback = Callable[[bool], Awaitable[None] | None]
LOGGER = logging.getLogger(__name__)


async def _run_callback(result: Awaitable[None] | None) -> None:
    """Await callback results only when they are genuinely awaitable."""

    if isawaitable(result):
        await result


class ReplySpeechSynthesizer(Protocol):
    """Protocol for text-to-speech backends that return raw PCM16 audio."""

    async def synthesize_pcm16(self, text: str) -> bytes:
        """Return 24 kHz mono PCM16 audio bytes for one text reply."""


@dataclass(slots=True)
class OpenAIReplySpeechSynthesizer:
    """Thin OpenAI TTS adapter returning 24 kHz PCM16 audio."""

    model: str
    voice: str
    api_key: str
    base_url: str = ""
    instructions: str = ""
    speed: float = 1.0
    _client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is not None:
            return self._client

        kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url.strip():
            kwargs["base_url"] = self.base_url.strip()
        self._client = AsyncOpenAI(**kwargs)
        return self._client

    def _build_request_kwargs(self, text: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "input": text,
            "model": self.model,
            "voice": self.voice,
            "response_format": "pcm",
            "speed": float(self.speed),
        }
        if str(self.instructions or "").strip():
            kwargs["instructions"] = str(self.instructions).strip()
        return kwargs

    async def synthesize_pcm16(self, text: str) -> bytes:
        """Synthesize one reply into 24 kHz mono PCM16 audio."""

        resolved_text = str(text or "").strip()
        if not resolved_text:
            return b""

        response = await self._get_client().audio.speech.create(
            **self._build_request_kwargs(resolved_text),
        )
        return response.read()

    async def stream_pcm16(self, text: str) -> AsyncIterator[bytes]:
        """Stream one reply as 24 kHz PCM16 chunks."""

        resolved_text = str(text or "").strip()
        if not resolved_text:
            return

        kwargs = self._build_request_kwargs(resolved_text)
        kwargs["stream_format"] = "audio"
        async with self._get_client().audio.speech.with_streaming_response.create(
            **kwargs,
        ) as response:
            async for chunk in response.iter_bytes():
                if chunk:
                    yield bytes(chunk)


@dataclass(slots=True)
class MacOSSayReplySpeechSynthesizer:
    """Local macOS TTS adapter that renders text with ``say`` and converts to PCM16."""

    voice: str = "Tingting"
    speed: float = 1.0

    async def synthesize_pcm16(self, text: str) -> bytes:
        """Render one reply with ``say`` and convert it to 24 kHz mono PCM16."""

        resolved_text = str(text or "").strip()
        if not resolved_text:
            return b""

        if shutil.which("say") is None:
            raise RuntimeError("Speech provider 'macos_say' requires the `say` command.")
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("Speech provider 'macos_say' requires the `ffmpeg` command.")

        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as handle:
            output_path = Path(handle.name)
        try:
            LOGGER.info(
                "macOS say synthesis started: voice=%s chars=%s speed=%.2f",
                self.voice,
                len(resolved_text),
                float(self.speed),
            )
            say_command = ["say"]
            if str(self.voice or "").strip():
                say_command.extend(["-v", str(self.voice).strip()])
            rate_wpm = max(80, min(360, int(round(175.0 * max(float(self.speed), 0.2)))))
            say_command.extend(["-r", str(rate_wpm), "-o", str(output_path), resolved_text])

            say_process = await asyncio.create_subprocess_exec(
                *say_command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, say_stderr = await say_process.communicate()
            if say_process.returncode != 0:
                error_text = (say_stderr or b"").decode("utf-8", errors="ignore").strip()
                raise RuntimeError(
                    f"`say` failed: {error_text or f'exit code {say_process.returncode}'}"
                )

            ffmpeg_process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(output_path),
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                "-ac",
                "1",
                "-ar",
                str(PCM_SAMPLE_RATE_HZ),
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            ffmpeg_stdout, ffmpeg_stderr = await ffmpeg_process.communicate()
            if ffmpeg_process.returncode != 0:
                error_text = (ffmpeg_stderr or b"").decode("utf-8", errors="ignore").strip()
                raise RuntimeError(
                    f"`ffmpeg` failed: {error_text or f'exit code {ffmpeg_process.returncode}'}"
                )
            LOGGER.info(
                "macOS say synthesis finished: voice=%s pcm_bytes=%s",
                self.voice,
                len(ffmpeg_stdout or b""),
            )
            return bytes(ffmpeg_stdout or b"")
        finally:
            output_path.unlink(missing_ok=True)


@dataclass(slots=True)
class ReplyAudioPlayer:
    """Play synthesized PCM16 audio through Reachy media and speech motion."""

    media: Any
    speech_driver: SpeechDriver | None = None
    input_sample_rate_hz: int = PCM_SAMPLE_RATE_HZ
    chunk_ms: int = 80

    async def play_pcm16(
        self,
        pcm_data: bytes,
        *,
        on_started: ReplyAudioEventCallback | None = None,
        on_audio_delta: ReplyAudioDeltaCallback | None = None,
        on_finished: ReplyAudioFinishedCallback | None = None,
        interrupt_event: asyncio.Event | None = None,
    ) -> bool:
        """Play one PCM16 reply and mirror it into speech motion when available."""

        async def _one_chunk() -> AsyncIterator[bytes]:
            if pcm_data:
                yield bytes(pcm_data)

        return await self.play_pcm16_stream(
            _one_chunk(),
            on_started=on_started,
            on_audio_delta=on_audio_delta,
            on_finished=on_finished,
            interrupt_event=interrupt_event,
        )

    async def play_pcm16_stream(
        self,
        chunks: AsyncIterable[bytes],
        *,
        on_started: ReplyAudioEventCallback | None = None,
        on_audio_delta: ReplyAudioDeltaCallback | None = None,
        on_finished: ReplyAudioFinishedCallback | None = None,
        interrupt_event: asyncio.Event | None = None,
    ) -> bool:
        """Play a streamed PCM16 reply while mirroring chunks into speech motion."""

        if not self._has_media_output():
            return False

        input_chunk_samples = max(
            1,
            int(self.input_sample_rate_hz * max(int(self.chunk_ms), 20) / 1000),
        )
        output_sample_rate_hz = self._resolve_output_sample_rate()
        pending = bytearray()
        played_any = False
        started_playing = False
        interrupted = False

        try:
            async for chunk in chunks:
                if self._is_interrupted(interrupt_event):
                    interrupted = True
                    break
                if not chunk:
                    continue
                pending.extend(chunk)
                while True:
                    if self._is_interrupted(interrupt_event):
                        interrupted = True
                        break
                    raw_chunk = self._take_pcm16_chunk(
                        pending,
                        preferred_sample_count=input_chunk_samples,
                    )
                    if raw_chunk is None:
                        break
                    if not started_playing:
                        self.media.start_playing()
                        started_playing = True
                        if on_started is not None:
                            await _run_callback(on_started())
                    interrupted = await self._play_raw_chunk(
                        raw_chunk,
                        output_sample_rate_hz=output_sample_rate_hz,
                        on_audio_delta=on_audio_delta,
                        interrupt_event=interrupt_event,
                    )
                    played_any = True
                    if interrupted:
                        break
                if interrupted:
                    break

            raw_tail = None
            if not interrupted:
                raw_tail = self._take_pcm16_chunk(pending, preferred_sample_count=None)
            if raw_tail is not None and not self._is_interrupted(interrupt_event):
                if not started_playing:
                    self.media.start_playing()
                    started_playing = True
                    if on_started is not None:
                        await _run_callback(on_started())
                interrupted = await self._play_raw_chunk(
                    raw_tail,
                    output_sample_rate_hz=output_sample_rate_hz,
                    on_audio_delta=on_audio_delta,
                    interrupt_event=interrupt_event,
                )
                played_any = True
            return played_any
        finally:
            speech_driver = self.speech_driver
            if speech_driver is not None and speech_driver.speech_active:
                speech_driver.reset_speech_motion()
            if started_playing:
                self.media.stop_playing()
                if on_finished is not None:
                    await _run_callback(on_finished(played_any and not interrupted))

    def _has_media_output(self) -> bool:
        return all(
            hasattr(self.media, attribute)
            for attribute in ("start_playing", "push_audio_sample", "stop_playing")
        )

    def _resolve_output_sample_rate(self) -> int:
        if self.media is None or not hasattr(self.media, "get_output_audio_samplerate"):
            return self.input_sample_rate_hz
        try:
            sample_rate = int(self.media.get_output_audio_samplerate())
        except Exception:
            return self.input_sample_rate_hz
        return sample_rate if sample_rate > 0 else self.input_sample_rate_hz

    @staticmethod
    def _encode_audio_delta(raw_chunk: NDArray[np.int16]) -> str:
        return base64.b64encode(
            raw_chunk.astype(np.int16, copy=False).tobytes()
        ).decode("ascii")

    def _feed_speech_motion(self, delta_b64: str) -> None:
        speech_driver = self.speech_driver
        if speech_driver is None:
            return
        speech_driver.feed_audio_delta(delta_b64)

    async def _play_raw_chunk(
        self,
        raw_chunk: NDArray[np.int16],
        *,
        output_sample_rate_hz: int,
        on_audio_delta: ReplyAudioDeltaCallback | None = None,
        interrupt_event: asyncio.Event | None = None,
    ) -> bool:
        if self._is_interrupted(interrupt_event):
            return True
        delta_b64 = self._encode_audio_delta(raw_chunk)
        self._feed_speech_motion(delta_b64)
        if on_audio_delta is not None:
            await _run_callback(on_audio_delta(delta_b64))
        playback_chunk = self._prepare_playback_chunk(
            raw_chunk,
            output_sample_rate_hz=output_sample_rate_hz,
        )
        self.media.push_audio_sample(playback_chunk)
        return await self._wait_for_chunk_playback(
            duration_s=float(playback_chunk.shape[0]) / float(output_sample_rate_hz),
            interrupt_event=interrupt_event,
        )

    @staticmethod
    def _is_interrupted(interrupt_event: asyncio.Event | None) -> bool:
        return interrupt_event is not None and interrupt_event.is_set()

    @staticmethod
    async def _wait_for_chunk_playback(
        *,
        duration_s: float,
        interrupt_event: asyncio.Event | None = None,
    ) -> bool:
        if duration_s <= 0.0:
            return ReplyAudioPlayer._is_interrupted(interrupt_event)
        if interrupt_event is None:
            await asyncio.sleep(duration_s)
            return False
        try:
            await asyncio.wait_for(interrupt_event.wait(), timeout=duration_s)
            return True
        except asyncio.TimeoutError:
            return False

    @staticmethod
    def _take_pcm16_chunk(
        pending: bytearray,
        *,
        preferred_sample_count: int | None,
    ) -> NDArray[np.int16] | None:
        if len(pending) < 2:
            return None

        if preferred_sample_count is None:
            sample_bytes = len(pending) - (len(pending) % 2)
        else:
            target_bytes = max(int(preferred_sample_count), 1) * 2
            if len(pending) < target_bytes:
                return None
            sample_bytes = target_bytes

        if sample_bytes <= 0:
            return None

        raw_chunk = np.frombuffer(bytes(pending[:sample_bytes]), dtype=np.int16).copy()
        del pending[:sample_bytes]
        return raw_chunk if raw_chunk.size > 0 else None

    def _prepare_playback_chunk(
        self,
        raw_chunk: NDArray[np.int16],
        *,
        output_sample_rate_hz: int,
    ) -> NDArray[np.float32]:
        float_chunk = raw_chunk.astype(np.float32) / 32768.0
        if (
            output_sample_rate_hz <= 0
            or output_sample_rate_hz == self.input_sample_rate_hz
            or float_chunk.shape[0] <= 1
        ):
            return np.asarray(float_chunk, dtype=np.float32)

        target_size = max(
            1,
            int(round(float_chunk.shape[0] * output_sample_rate_hz / self.input_sample_rate_hz)),
        )
        return np.asarray(resample(float_chunk, target_size), dtype=np.float32)


@dataclass(slots=True)
class RuntimeReplyAudioService:
    """Synthesize one final reply and play it through the configured media path."""

    synthesizer: ReplySpeechSynthesizer
    player: ReplyAudioPlayer
    _active_interrupt_event: asyncio.Event | None = field(default=None, init=False, repr=False)
    _active_playback_task: asyncio.Task[bool] | None = field(default=None, init=False, repr=False)

    async def speak_text(
        self,
        text: str,
        *,
        on_started: ReplyAudioEventCallback | None = None,
        on_audio_delta: ReplyAudioDeltaCallback | None = None,
        on_finished: ReplyAudioFinishedCallback | None = None,
    ) -> bool:
        """Synthesize and play one final reply."""

        resolved_text = str(text or "").strip()
        if not resolved_text:
            return False

        interrupt_event = asyncio.Event()
        active_task = asyncio.current_task()
        if active_task is not None:
            self._active_playback_task = active_task
        self._active_interrupt_event = interrupt_event

        try:
            stream_pcm16 = getattr(self.synthesizer, "stream_pcm16", None)
            if callable(stream_pcm16):
                LOGGER.info(
                    "Reply audio streaming started: synthesizer=%s chars=%s",
                    type(self.synthesizer).__name__,
                    len(resolved_text),
                )
                return await self.player.play_pcm16_stream(
                    stream_pcm16(resolved_text),
                    on_started=on_started,
                    on_audio_delta=on_audio_delta,
                    on_finished=on_finished,
                    interrupt_event=interrupt_event,
                )

            LOGGER.info(
                "Reply audio synthesis started: synthesizer=%s chars=%s",
                type(self.synthesizer).__name__,
                len(resolved_text),
            )
            pcm_data = await self.synthesizer.synthesize_pcm16(resolved_text)
            if interrupt_event.is_set() or not pcm_data:
                LOGGER.info(
                    "Reply audio synthesis produced no playable audio: interrupted=%s pcm_bytes=%s",
                    interrupt_event.is_set(),
                    len(pcm_data),
                )
                return False
            played = await self.player.play_pcm16(
                pcm_data,
                on_started=on_started,
                on_audio_delta=on_audio_delta,
                on_finished=on_finished,
                interrupt_event=interrupt_event,
            )
            LOGGER.info(
                "Reply audio playback finished: played=%s pcm_bytes=%s",
                played,
                len(pcm_data),
            )
            return played
        finally:
            if self._active_playback_task is active_task:
                self._active_playback_task = None
            if self._active_interrupt_event is interrupt_event:
                self._active_interrupt_event = None

    async def interrupt_playback(self) -> bool:
        """Request interruption of the active reply-audio playback, if any."""

        active_task = self._active_playback_task
        interrupt_event = self._active_interrupt_event
        if active_task is None or active_task.done() or interrupt_event is None:
            return False
        interrupt_event.set()
        await asyncio.sleep(0)
        return True


def build_runtime_reply_audio_service(
    *,
    config: SpeechRuntimeConfig,
    media: Any | None,
    speech_driver: SpeechDriver | None = None,
    fallback_api_key: str = "",
) -> RuntimeReplyAudioService | None:
    """Build the optional reply-audio service for one resident runtime."""

    if not config.enabled or media is None:
        return None
    if not all(
        hasattr(media, attribute)
        for attribute in ("start_playing", "push_audio_sample", "stop_playing")
    ):
        return None

    provider = str(config.provider or "").strip().lower()
    if provider == "openai":
        api_key = str(config.api_key or "").strip() or str(fallback_api_key or "").strip()
        if not api_key:
            raise RuntimeError("Speech provider 'openai' requires `api_key`.")

        synthesizer: ReplySpeechSynthesizer = OpenAIReplySpeechSynthesizer(
            model=str(config.model or "").strip() or SpeechRuntimeConfig().model,
            voice=str(config.voice or "").strip() or SpeechRuntimeConfig().voice,
            api_key=api_key,
            base_url=str(config.base_url or "").strip(),
            instructions=str(config.instructions or "").strip(),
            speed=float(config.speed),
        )
    elif provider == "macos_say":
        synthesizer = MacOSSayReplySpeechSynthesizer(
            voice=str(config.voice or "").strip() or "Tingting",
            speed=float(config.speed),
        )
    else:
        raise RuntimeError(
            f"Unsupported speech provider: {config.provider or '<empty>'}"
        )

    return RuntimeReplyAudioService(
        synthesizer=synthesizer,
        player=ReplyAudioPlayer(
            media=media,
            speech_driver=speech_driver,
            chunk_ms=max(int(config.chunk_ms), 20),
        ),
    )


__all__ = [
    "MacOSSayReplySpeechSynthesizer",
    "OpenAIReplySpeechSynthesizer",
    "PCM_SAMPLE_RATE_HZ",
    "ReplyAudioPlayer",
    "ReplySpeechSynthesizer",
    "RuntimeReplyAudioService",
    "build_runtime_reply_audio_service",
]
