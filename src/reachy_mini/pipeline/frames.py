"""Immutable frame contracts for the v4 pipeline path."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from reachy_mini.action_runtime import ActionResult


@dataclass(frozen=True)
class AudioFrame:
    """Raw PCM audio input."""

    pcm: bytes
    sample_rate: int
    channels: int
    ts_ms: int


@dataclass(frozen=True)
class SpeechActivityFrame:
    """VAD activity boundary."""

    state: str
    ts_ms: int


@dataclass(frozen=True)
class TranscriptionFrame:
    """Speech-to-text result."""

    text: str
    is_final: bool
    turn_id: str
    lang: str = ""


@dataclass(frozen=True)
class VisionEventFrame:
    """Vision/tracker event passed to Brain context."""

    event: str
    payload: dict[str, Any]
    ts_ms: int


@dataclass(frozen=True)
class CameraFrame:
    """Browser-provided camera frame for local reactive vision."""

    image_b64: str
    mime_type: str
    width: int
    height: int
    ts_ms: int


@dataclass(frozen=True)
class TickFrame:
    """Internal timer tick."""

    ts_ms: int
    tick_id: int


@dataclass(frozen=True)
class BrowserInputFrame:
    """Web UI input frame."""

    kind: str
    payload: dict[str, Any]
    session_id: str


@dataclass(frozen=True)
class SDKMessageFrame:
    """Claude Agent SDK message forwarded through the pipeline."""

    message: Any
    turn_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TextFrame:
    """Text output for logs or UI, not TTS."""

    text: str
    turn_id: str


@dataclass(frozen=True)
class TTSAudioFrame:
    """Synthesized TTS audio chunk."""

    pcm: bytes
    sample_rate: int
    turn_id: str
    is_final: bool


@dataclass(frozen=True)
class ActionResultFrame:
    """Pipeline action execution result."""

    request_id: str
    name: str
    owner_id: str
    status: str
    error: str | None
    duration_ms: int
    result: Any = None

    @classmethod
    def from_result(cls, result: ActionResult) -> "ActionResultFrame":
        """Create a frame from an ActionResult."""
        return cls(
            request_id=result.request_id,
            name=result.name,
            owner_id=result.owner_id,
            status=result.status,
            result=result.result,
            error=result.error,
            duration_ms=result.duration_ms,
        )


@dataclass(frozen=True)
class EmbodimentFrame:
    """Body-agnostic expression event for robots, 3D bodies, and avatars."""

    action: str
    payload: dict[str, Any]
    target: str = "all"
    turn_id: str = ""
    ts_ms: int = 0


@dataclass(frozen=True)
class WorkerEventFrame:
    """Worker status frame."""

    task_id: str
    event: str
    payload: dict[str, Any]
    ts_ms: int


@dataclass(frozen=True)
class SpeechPresenterFrame:
    """Text chunk prepared for TTS."""

    text: str
    style: dict[str, Any]
    turn_id: str
    chunk_index: int
    is_final: bool


@dataclass(frozen=True)
class InterruptFrame:
    """Barge-in or explicit interrupt signal."""

    scope: str
    turn_id: str = ""
    reason: str = ""
    ts_ms: int = 0


@dataclass(frozen=True)
class TTSStopFrame:
    """Signal to stop TTS synthesis/playback."""

    turn_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class PipelineErrorFrame:
    """Non-fatal adapter error frame."""

    component: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)
