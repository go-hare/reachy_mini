"""Voice service — push-to-talk recording + STT integration.

Ported from Claude Code's ``services/voice.ts``.

Audio capture with fallback chain:
1. ``sounddevice`` (PortAudio binding) — best cross-platform option
2. ``arecord`` (ALSA) — Linux fallback
3. ``sox rec`` — universal CLI fallback

Recording parameters: 16 kHz, 16-bit signed, mono PCM (WAV).
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import platform
import shutil
import struct
import subprocess
import tempfile
import time
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from ..paths import mini_agent_path

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit
SILENCE_THRESHOLD = 0.03
SILENCE_DURATION_S = 2.0
MAX_RECORD_SECONDS = 120


class AudioBackend(str, Enum):
    SOUNDDEVICE = "sounddevice"
    ARECORD = "arecord"
    SOX = "sox"
    NONE = "none"


class VoiceError(Exception):
    """Voice subsystem error."""


# ── Dependency checks ────────────────────────────────────────────────


def _has_sounddevice() -> bool:
    try:
        import sounddevice  # noqa: F401
        return True
    except ImportError:
        return False


def _has_arecord() -> bool:
    if platform.system() != "Linux":
        return False
    return shutil.which("arecord") is not None


def _has_sox() -> bool:
    return shutil.which("sox") is not None or shutil.which("rec") is not None


def check_recording_availability() -> AudioBackend:
    """Return the best available audio backend."""
    if _has_sounddevice():
        return AudioBackend.SOUNDDEVICE
    if _has_arecord():
        return AudioBackend.ARECORD
    if _has_sox():
        return AudioBackend.SOX
    return AudioBackend.NONE


def check_voice_dependencies() -> dict[str, Any]:
    """Check all voice dependencies and return a status dict."""
    backend = check_recording_availability()
    return {
        "backend": backend.value,
        "available": backend != AudioBackend.NONE,
        "sounddevice": _has_sounddevice(),
        "arecord": _has_arecord(),
        "sox": _has_sox(),
        "platform": platform.system(),
    }


# ── Recording state ─────────────────────────────────────────────────


@dataclass
class RecordingState:
    """Mutable state for an active recording session."""
    is_recording: bool = False
    backend: AudioBackend = AudioBackend.NONE
    start_time: float = 0.0
    frames: list[bytes] = field(default_factory=list)
    process: subprocess.Popen[bytes] | None = None
    temp_path: str = ""
    _sd_stream: Any = None


_state = RecordingState()


def is_recording() -> bool:
    return _state.is_recording


# ── Recording — sounddevice backend ─────────────────────────────────


def _start_sounddevice() -> None:
    import sounddevice as sd

    _state.frames.clear()

    def callback(indata: Any, frame_count: int, time_info: Any, status: Any) -> None:
        if status:
            logger.debug("sounddevice status: %s", status)
        _state.frames.append(bytes(indata))

    stream = sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
        callback=callback,
        blocksize=1024,
    )
    stream.start()
    _state._sd_stream = stream


def _stop_sounddevice() -> bytes:
    stream = _state._sd_stream
    if stream is not None:
        stream.stop()
        stream.close()
        _state._sd_stream = None
    return b"".join(_state.frames)


# ── Recording — arecord backend ─────────────────────────────────────


def _start_arecord() -> None:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    _state.temp_path = tmp.name
    tmp.close()
    _state.process = subprocess.Popen(
        [
            "arecord",
            "-f", "S16_LE",
            "-r", str(SAMPLE_RATE),
            "-c", str(CHANNELS),
            "-t", "wav",
            _state.temp_path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_arecord() -> bytes:
    proc = _state.process
    if proc is not None:
        proc.terminate()
        proc.wait(timeout=5)
        _state.process = None
    path = Path(_state.temp_path)
    if path.exists():
        data = path.read_bytes()
        path.unlink(missing_ok=True)
        return data
    return b""


# ── Recording — sox backend ─────────────────────────────────────────


def _start_sox() -> None:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    _state.temp_path = tmp.name
    tmp.close()
    rec_bin = shutil.which("rec") or "sox"
    args: list[str]
    if rec_bin.endswith("rec"):
        args = [
            rec_bin,
            "-r", str(SAMPLE_RATE),
            "-c", str(CHANNELS),
            "-b", "16",
            "-e", "signed-integer",
            _state.temp_path,
            "silence", "1", "0.1", f"{SILENCE_THRESHOLD * 100}%",
            "1", str(SILENCE_DURATION_S), f"{SILENCE_THRESHOLD * 100}%",
        ]
    else:
        args = [
            rec_bin,
            "-d",
            "-r", str(SAMPLE_RATE),
            "-c", str(CHANNELS),
            "-b", "16",
            "-e", "signed-integer",
            "-t", "wav",
            _state.temp_path,
        ]
    _state.process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_sox() -> bytes:
    return _stop_arecord()  # same cleanup logic


# ── Public API ───────────────────────────────────────────────────────


def start_recording(backend: AudioBackend | None = None) -> AudioBackend:
    """Start recording audio.

    Returns the backend that was used. Raises ``VoiceError`` if no
    backend is available.
    """
    if _state.is_recording:
        raise VoiceError("Already recording")

    if backend is None:
        backend = check_recording_availability()

    if backend == AudioBackend.NONE:
        raise VoiceError(
            "No audio recording backend available. "
            "Install sounddevice (pip install sounddevice) or sox."
        )

    _state.backend = backend
    _state.is_recording = True
    _state.start_time = time.monotonic()

    try:
        if backend == AudioBackend.SOUNDDEVICE:
            _start_sounddevice()
        elif backend == AudioBackend.ARECORD:
            _start_arecord()
        elif backend == AudioBackend.SOX:
            _start_sox()
    except Exception as exc:
        _state.is_recording = False
        raise VoiceError(f"Failed to start recording with {backend.value}: {exc}") from exc

    logger.info("Recording started (backend=%s)", backend.value)
    return backend


def stop_recording() -> bytes:
    """Stop recording and return WAV data.

    Returns raw WAV bytes (header + PCM data).
    """
    if not _state.is_recording:
        raise VoiceError("Not recording")

    _state.is_recording = False
    duration = time.monotonic() - _state.start_time
    logger.info("Recording stopped (%.1fs)", duration)

    try:
        if _state.backend == AudioBackend.SOUNDDEVICE:
            raw_pcm = _stop_sounddevice()
            return _pcm_to_wav(raw_pcm)
        elif _state.backend == AudioBackend.ARECORD:
            return _stop_arecord()
        elif _state.backend == AudioBackend.SOX:
            return _stop_sox()
    except Exception as exc:
        raise VoiceError(f"Failed to stop recording: {exc}") from exc

    return b""


def cancel_recording() -> None:
    """Cancel an active recording without returning data."""
    if not _state.is_recording:
        return
    _state.is_recording = False
    try:
        if _state.backend == AudioBackend.SOUNDDEVICE:
            _stop_sounddevice()
        elif _state.backend in (AudioBackend.ARECORD, AudioBackend.SOX):
            proc = _state.process
            if proc is not None:
                proc.kill()
                proc.wait(timeout=3)
                _state.process = None
            if _state.temp_path:
                Path(_state.temp_path).unlink(missing_ok=True)
    except Exception:
        pass
    logger.info("Recording cancelled")


# ── STT integration ─────────────────────────────────────────────────

STTCallback = Callable[[bytes], str]

_stt_callback: STTCallback | None = None


def register_stt(callback: STTCallback) -> None:
    """Register a speech-to-text callback.

    The callback receives WAV bytes and returns transcribed text.
    """
    global _stt_callback
    _stt_callback = callback


def has_stt_callback() -> bool:
    return _stt_callback is not None


async def transcribe_wav(wav_data: bytes) -> str:
    """Transcribe already-recorded WAV bytes using the registered STT callback."""
    if _stt_callback is None:
        raise VoiceError("No STT callback registered. Call register_stt() first.")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _stt_callback, wav_data)


async def record_and_transcribe(
    *,
    backend: AudioBackend | None = None,
    timeout: float = MAX_RECORD_SECONDS,
) -> str:
    """Record until stopped, then transcribe.

    Call ``stop_recording()`` from another coroutine/thread to end.
    This is a convenience wrapper for push-to-talk flows.
    """
    if _stt_callback is None:
        raise VoiceError("No STT callback registered. Call register_stt() first.")

    start_recording(backend)

    start = time.monotonic()
    while _state.is_recording:
        if time.monotonic() - start > timeout:
            break
        await asyncio.sleep(0.1)

    wav_data = stop_recording()
    if not wav_data:
        return ""

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _stt_callback, wav_data)


# ── Helpers ──────────────────────────────────────────────────────────


def _pcm_to_wav(pcm_data: bytes) -> bytes:
    """Wrap raw PCM data in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def get_recording_duration() -> float:
    """Elapsed seconds of current recording (0 if not recording)."""
    if not _state.is_recording:
        return 0.0
    return time.monotonic() - _state.start_time


# ── Device probing ──────────────────────────────────────────────────


@dataclass(slots=True)
class AudioDevice:
    """Describes a detected audio input device."""

    name: str
    id: str | int
    sample_rate: int = 0
    channels: int = 0


def _probe_alsa_devices() -> list[AudioDevice]:
    """Parse ``arecord -l`` output to list ALSA capture devices."""
    if not _has_arecord():
        return []
    try:
        result = subprocess.run(
            ["arecord", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []

        import re

        devices: list[AudioDevice] = []
        for line in result.stdout.splitlines():
            m = re.match(
                r"card\s+(\d+):\s+(\w+)\s+\[(.+?)\],\s+device\s+(\d+):\s+(.+)",
                line,
            )
            if m:
                card_num, _card_id, card_name, dev_num, dev_desc = m.groups()
                devices.append(AudioDevice(
                    name=f"{card_name} — {dev_desc.strip()}",
                    id=f"hw:{card_num},{dev_num}",
                    sample_rate=SAMPLE_RATE,
                    channels=CHANNELS,
                ))
        return devices
    except Exception as exc:
        logger.debug("ALSA device probing failed: %s", exc)
        return []


def _probe_sounddevice_devices() -> list[AudioDevice]:
    """List capture devices via ``sounddevice.query_devices()``."""
    if not _has_sounddevice():
        return []
    try:
        import sounddevice as sd

        devices: list[AudioDevice] = []
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:  # type: ignore[union-attr]
                devices.append(AudioDevice(
                    name=dev["name"],  # type: ignore[index]
                    id=idx,
                    sample_rate=int(dev.get("default_samplerate", 0)),  # type: ignore[arg-type]
                    channels=int(dev.get("max_input_channels", 0)),  # type: ignore[arg-type]
                ))
        return devices
    except Exception as exc:
        logger.debug("sounddevice device probing failed: %s", exc)
        return []


def probe_audio_devices() -> list[AudioDevice]:
    """Return a list of available audio input devices.

    Tries PortAudio (sounddevice) first, falls back to ALSA parsing.
    """
    devices = _probe_sounddevice_devices()
    if devices:
        return devices
    return _probe_alsa_devices()


# ── ALSA card detection ─────────────────────────────────────────────

_alsa_cards_memo: bool | None = None


def has_alsa_cards() -> bool:
    """Check if ``/proc/asound/cards`` exists and lists real cards.

    Memoized — card presence doesn't change mid-session.
    """
    global _alsa_cards_memo
    if _alsa_cards_memo is not None:
        return _alsa_cards_memo

    cards_path = Path("/proc/asound/cards")
    if not cards_path.exists():
        _alsa_cards_memo = False
        return False

    try:
        content = cards_path.read_text(encoding="utf-8").strip()
        _alsa_cards_memo = bool(content) and "no soundcards" not in content
    except OSError:
        _alsa_cards_memo = False

    return _alsa_cards_memo


# ── Microphone permission request ──────────────────────────────────


def request_microphone_permission() -> bool:
    """Attempt to request or verify microphone access on the current OS.

    - **macOS**: Triggers the TCC permission dialog via a short probe recording.
    - **Linux**: Checks PulseAudio / PipeWire availability.
    - **Windows**: Checks microphone privacy setting via PowerShell.

    Returns True if access appears to be granted.
    """
    system = platform.system()

    if system == "Darwin":
        return _request_mic_macos()
    elif system == "Linux":
        return _request_mic_linux()
    elif system == "Windows":
        return _request_mic_windows()

    return True  # unknown platform — assume accessible


def _request_mic_macos() -> bool:
    """macOS: trigger the TCC dialog by doing a short probe recording."""
    backend = check_recording_availability()
    if backend == AudioBackend.NONE:
        return False
    try:
        start_recording(backend)
        time.sleep(0.15)
        stop_recording()
        return True
    except VoiceError:
        return False


def _request_mic_linux() -> bool:
    """Linux: verify PulseAudio or PipeWire is available and responsive."""
    pa = shutil.which("pactl")
    if pa:
        try:
            r = subprocess.run(
                ["pactl", "info"],
                capture_output=True, timeout=3,
            )
            if r.returncode == 0:
                return True
        except Exception:
            pass

    pw = shutil.which("pw-cli")
    if pw:
        try:
            r = subprocess.run(
                ["pw-cli", "info", "0"],
                capture_output=True, timeout=3,
            )
            return r.returncode == 0
        except Exception:
            pass

    return has_alsa_cards() if platform.system() == "Linux" else False


def _request_mic_windows() -> bool:
    """Windows: check microphone privacy setting via PowerShell."""
    try:
        result = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "Get-ItemPropertyValue "
                "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
                "\\CapabilityAccessManager\\ConsentStore\\microphone' "
                "-Name Value",
            ],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip().lower() == "allow"
    except Exception:
        return True  # can't determine — assume allowed


# ── WSL audio detection ────────────────────────────────────────────


@dataclass(slots=True)
class WslAudioStatus:
    """Describes audio availability inside WSL."""

    has_audio: bool
    backend: str  # "pulseaudio", "pipewire", "none"
    notes: str = ""


def detect_wsl_audio() -> WslAudioStatus:
    """Detect audio availability in a WSL environment.

    - **WSLg** (Win 11 default): PulseAudio socket exists at
      ``/mnt/wslg/PulseServer`` — audio works.
    - **WSL1 / Win10-WSL2 without WSLg**: no audio support.
    """
    if platform.system() != "Linux":
        return WslAudioStatus(
            has_audio=False, backend="none",
            notes="Not running on Linux",
        )

    wslg_pulse = Path("/mnt/wslg/PulseServer")
    pulse_env = os.environ.get("PULSE_SERVER", "")

    if wslg_pulse.exists() or "wslg" in pulse_env.lower():
        return WslAudioStatus(
            has_audio=True,
            backend="pulseaudio",
            notes="WSLg PulseAudio detected (Windows 11)",
        )

    wsl_interop = Path("/proc/sys/fs/binfmt_misc/WSLInterop")
    wsl_env = os.environ.get("WSL_DISTRO_NAME", "")

    if wsl_interop.exists() or wsl_env:
        return WslAudioStatus(
            has_audio=False,
            backend="none",
            notes="WSL detected but no WSLg audio. "
                  "Run Claude Code natively on Windows or upgrade to Windows 11 WSLg.",
        )

    return WslAudioStatus(
        has_audio=False, backend="none",
        notes="Not a WSL environment",
    )


# ── Keyterm detection ───────────────────────────────────────────────


class VoiceKeyterms:
    """Detects wake words or key terms in transcribed text.

    Default terms: ``"hey agent"``, ``"okay agent"``, ``"start"``,
    ``"stop"``, ``"cancel"``.

    Custom terms can be loaded from ``~/.mini_agent/voice_keyterms.json``
    (a JSON list of strings).
    """

    DEFAULT_TERMS: list[str] = [
        "hey agent",
        "okay agent",
        "start",
        "stop",
        "cancel",
    ]

    GLOBAL_STT_KEYTERMS: list[str] = [
        "MCP",
        "symlink",
        "grep",
        "regex",
        "localhost",
        "codebase",
        "TypeScript",
        "JSON",
        "OAuth",
        "webhook",
        "gRPC",
        "dotfiles",
        "subagent",
        "worktree",
    ]

    def __init__(
        self,
        custom_terms: list[str] | None = None,
        *,
        config_path: Path | None = None,
    ) -> None:
        self._terms: list[str] = list(self.DEFAULT_TERMS)
        if custom_terms is not None:
            self._terms = custom_terms
        elif config_path is None:
            config_path = mini_agent_path("voice_keyterms.json")

        if config_path and custom_terms is None:
            self._load_config(config_path)

    def _load_config(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            import json as _json
            data = _json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._terms = [str(t) for t in data if t]
        except Exception as exc:
            logger.debug("Failed to load voice keyterms config: %s", exc)

    @property
    def terms(self) -> list[str]:
        return list(self._terms)

    def check_keyterms(self, text: str) -> str | None:
        """Check if *text* contains any keyterm.

        Returns the matched keyterm (lowercase) or ``None``.
        """
        normalised = text.strip().lower()
        for term in self._terms:
            if term.lower() in normalised:
                return term.lower()
        return None

    @staticmethod
    def split_identifier(name: str) -> list[str]:
        """Split camelCase / kebab-case / snake_case identifiers into words.

        Fragments <= 2 characters are discarded.
        """
        import re

        parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
        return [
            w.strip()
            for w in re.split(r"[-_./\s]+", parts)
            if 2 < len(w.strip()) <= 20
        ]

    def get_stt_keyterms(
        self,
        *,
        project_name: str = "",
        branch_name: str = "",
        recent_files: set[str] | None = None,
        max_terms: int = 50,
    ) -> list[str]:
        """Build STT keyword hints from global terms + session context.

        Mirrors Claude Code's ``voiceKeyterms.ts``.
        """
        terms: set[str] = set(self.GLOBAL_STT_KEYTERMS)

        if project_name and 2 < len(project_name) <= 50:
            terms.add(project_name)

        if branch_name:
            for word in self.split_identifier(branch_name):
                terms.add(word)

        if recent_files:
            for fpath in recent_files:
                if len(terms) >= max_terms:
                    break
                stem = Path(fpath).stem
                for word in self.split_identifier(stem):
                    terms.add(word)

        return list(terms)[:max_terms]


# ── Streaming STT ───────────────────────────────────────────────────


class StreamingSTT:
    """Real-time speech-to-text that yields partial transcripts as audio
    is captured.

    Supports two backends:

    * **local** — chunked Whisper inference (requires ``faster-whisper``
      or ``openai-whisper``)
    * **api** — sends audio chunks to a remote STT endpoint

    Usage::

        stt = StreamingSTT(backend="local")
        async for partial in stt.start_stream():
            print(partial)  # intermediate transcripts
        final = stt.stop_stream()
    """

    def __init__(
        self,
        *,
        backend: str = "local",
        model_name: str = "base",
        api_url: str = "",
        api_key: str = "",
        chunk_duration_ms: int = 500,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
    ) -> None:
        self._backend = backend
        self._model_name = model_name
        self._api_url = api_url
        self._api_key = api_key
        self._chunk_duration_ms = chunk_duration_ms
        self._sample_rate = sample_rate
        self._channels = channels

        self._running = False
        self._audio_buffer: bytearray = bytearray()
        self._transcript_parts: list[str] = []
        self._final_transcript: str = ""
        self._whisper_model: Any = None
        self._recording_backend: AudioBackend = AudioBackend.NONE

    def _ensure_whisper(self) -> Any:
        """Lazy-load a local Whisper model."""
        if self._whisper_model is not None:
            return self._whisper_model
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]
            self._whisper_model = WhisperModel(self._model_name, compute_type="int8")
        except ImportError:
            try:
                import whisper  # type: ignore[import-untyped]
                self._whisper_model = whisper.load_model(self._model_name)
            except ImportError:
                raise VoiceError(
                    "Streaming STT requires faster-whisper or openai-whisper. "
                    "Install with: pip install faster-whisper"
                )
        return self._whisper_model

    def _transcribe_chunk_local(self, wav_data: bytes) -> str:
        """Transcribe a WAV chunk using the local Whisper model."""
        model = self._ensure_whisper()
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]
            if isinstance(model, WhisperModel):
                segments, _ = model.transcribe(
                    io.BytesIO(wav_data), language="en",
                )
                return " ".join(seg.text for seg in segments).strip()
        except (ImportError, TypeError):
            pass

        try:
            import whisper  # type: ignore[import-untyped]
            import numpy as np

            audio_array = np.frombuffer(wav_data[44:], dtype=np.int16).astype(np.float32) / 32768.0
            result = model.transcribe(audio_array, fp16=False)
            return result.get("text", "").strip()
        except Exception as exc:
            logger.debug("Local STT chunk failed: %s", exc)
            return ""

    async def _transcribe_chunk_api(self, wav_data: bytes) -> str:
        """Send a WAV chunk to a remote STT API."""
        if not self._api_url:
            raise VoiceError("API-based streaming STT requires api_url")

        import urllib.request

        headers = {"Content-Type": "audio/wav"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        req = urllib.request.Request(
            self._api_url, data=wav_data, headers=headers, method="POST",
        )

        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(req, timeout=10).read().decode(),
            )
            import json as _json
            data = _json.loads(resp)
            return data.get("text", "").strip()
        except Exception as exc:
            logger.debug("API STT chunk failed: %s", exc)
            return ""

    def _chunk_bytes(self) -> int:
        """Number of PCM bytes per chunk."""
        return int(self._sample_rate * SAMPLE_WIDTH * self._channels
                    * self._chunk_duration_ms / 1000)

    async def start_stream(self) -> AsyncIterator[str]:
        """Start recording and yield partial transcripts as they arrive.

        Call :meth:`stop_stream` from another coroutine to end.
        """
        self._running = True
        self._audio_buffer.clear()
        self._transcript_parts.clear()
        self._final_transcript = ""

        self._recording_backend = start_recording()
        chunk_size = self._chunk_bytes()

        try:
            while self._running:
                await asyncio.sleep(self._chunk_duration_ms / 1000.0)

                if len(self._audio_buffer) >= chunk_size:
                    chunk_pcm = bytes(self._audio_buffer[:chunk_size])
                    del self._audio_buffer[:chunk_size]
                    wav_chunk = _pcm_to_wav(chunk_pcm)

                    if self._backend == "api":
                        text = await self._transcribe_chunk_api(wav_chunk)
                    else:
                        loop = asyncio.get_event_loop()
                        text = await loop.run_in_executor(
                            None, self._transcribe_chunk_local, wav_chunk,
                        )

                    if text:
                        self._transcript_parts.append(text)
                        yield " ".join(self._transcript_parts)
        finally:
            self._running = False

    def stop_stream(self) -> str:
        """Stop the stream and return the final assembled transcript."""
        self._running = False
        try:
            remaining_wav = stop_recording()
            if remaining_wav and len(remaining_wav) > 44:
                tail = self._transcribe_chunk_local(remaining_wav)
                if tail:
                    self._transcript_parts.append(tail)
        except VoiceError:
            pass

        self._final_transcript = " ".join(self._transcript_parts).strip()
        return self._final_transcript


# ── Voice activity detection ────────────────────────────────────────


class VoiceActivityDetector:
    """Simple energy-based voice activity detector.

    Operates on raw 16-bit PCM audio. Used to trim silence from
    recordings and detect speech segments.
    """

    def __init__(
        self,
        *,
        energy_threshold: float = 0.02,
        min_speech_ms: int = 200,
        min_silence_ms: int = 300,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        self._energy_threshold = energy_threshold
        self._min_speech_ms = min_speech_ms
        self._min_silence_ms = min_silence_ms
        self._sample_rate = sample_rate

    def _rms_energy(self, pcm_chunk: bytes) -> float:
        """Compute RMS energy of a 16-bit PCM chunk (normalised 0-1)."""
        if len(pcm_chunk) < 2:
            return 0.0
        n_samples = len(pcm_chunk) // 2
        samples = struct.unpack(f"<{n_samples}h", pcm_chunk[:n_samples * 2])
        sum_sq = sum(s * s for s in samples)
        rms = (sum_sq / n_samples) ** 0.5
        return rms / 32768.0

    def is_speech(self, audio_chunk: bytes) -> bool:
        """Return True if *audio_chunk* (raw 16-bit PCM) contains speech."""
        return self._rms_energy(audio_chunk) >= self._energy_threshold

    def get_speech_segments(
        self, audio: bytes, *, frame_ms: int = 30,
    ) -> list[tuple[int, int]]:
        """Identify speech segments in *audio* (raw 16-bit PCM).

        Returns a list of ``(start_ms, end_ms)`` tuples.
        """
        frame_bytes = int(self._sample_rate * SAMPLE_WIDTH * frame_ms / 1000)
        total_frames = len(audio) // frame_bytes

        segments: list[tuple[int, int]] = []
        in_speech = False
        speech_start = 0
        silence_start = 0

        for i in range(total_frames):
            offset = i * frame_bytes
            chunk = audio[offset:offset + frame_bytes]
            current_ms = i * frame_ms

            if self._rms_energy(chunk) >= self._energy_threshold:
                if not in_speech:
                    in_speech = True
                    speech_start = current_ms
                silence_start = 0
            else:
                if in_speech:
                    if silence_start == 0:
                        silence_start = current_ms
                    if current_ms - silence_start >= self._min_silence_ms:
                        duration = silence_start - speech_start
                        if duration >= self._min_speech_ms:
                            segments.append((speech_start, silence_start))
                        in_speech = False
                        silence_start = 0

        if in_speech:
            end_ms = total_frames * frame_ms
            duration = end_ms - speech_start
            if duration >= self._min_speech_ms:
                segments.append((speech_start, end_ms))

        return segments

    def trim_silence(self, audio: bytes) -> bytes:
        """Return *audio* with leading and trailing silence removed."""
        segments = self.get_speech_segments(audio)
        if not segments:
            return b""

        start_ms = segments[0][0]
        end_ms = segments[-1][1]

        bytes_per_ms = self._sample_rate * SAMPLE_WIDTH // 1000
        start_byte = start_ms * bytes_per_ms
        end_byte = end_ms * bytes_per_ms

        return audio[start_byte:end_byte]
