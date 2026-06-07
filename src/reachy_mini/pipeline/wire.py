"""Frame ↔ websocket JSON serialization for the v4 pipeline."""

from __future__ import annotations

import base64
import binascii
from dataclasses import is_dataclass
from enum import Enum
from pathlib import PurePath
from typing import Any, Mapping

from reachy_mini.action_runtime import ActionResult
from reachy_mini.reachy_brain.pipecat_bridge import sdk_message_to_payload

from .frames import (
    ActionResultFrame,
    AudioFrame,
    BrowserInputFrame,
    CameraFrame,
    InterruptFrame,
    PipelineErrorFrame,
    SDKMessageFrame,
    SpeechActivityFrame,
    SpeechPresenterFrame,
    TextFrame,
    TickFrame,
    TranscriptionFrame,
    TTSAudioFrame,
    TTSStopFrame,
    VisionEventFrame,
    WorkerEventFrame,
)

LEGACY_INBOUND_PREFIXES = ("front_",)
LEGACY_INBOUND_TYPES = frozenset(
    {
        "user_text",
        "browser_audio_chunk",
        "browser_audio_stop",
    }
)
CAMERA_FRAME_MAX_B64_CHARS = 750_000
CAMERA_FRAME_MAX_DIMENSION = 1920
CAMERA_FRAME_MIME_TYPES = frozenset({"image/jpeg", "image/jpg"})


class WireSerializationError(ValueError):
    """Raised when an outbound frame cannot be encoded to JSON."""


class WireDecodeError(ValueError):
    """Raised when an inbound websocket message cannot be decoded."""


def encode_frame(frame: Any) -> dict[str, Any]:
    """Encode a v4 outbound frame as a websocket JSON envelope."""
    if isinstance(frame, SDKMessageFrame):
        return _envelope(
            "sdk_message",
            {
                **sdk_message_to_payload(frame.message),
                "turn_id": frame.turn_id,
                "metadata": _to_jsonable(frame.metadata),
            },
            ts_ms=_metadata_ts(frame.metadata),
        )
    if isinstance(frame, ActionResultFrame):
        return _envelope(
            "action_result",
            {
                "request_id": frame.request_id,
                "name": frame.name,
                "owner_id": frame.owner_id,
                "status": frame.status,
                "error": frame.error,
                "duration_ms": frame.duration_ms,
            },
        )
    if isinstance(frame, WorkerEventFrame):
        return _envelope(
            "worker_event",
            {
                "task_id": frame.task_id,
                "event": frame.event,
                "payload": _to_jsonable(frame.payload),
            },
            ts_ms=frame.ts_ms,
        )
    if isinstance(frame, TTSAudioFrame):
        return _envelope(
            "tts_audio",
            {
                "pcm_b64": _b64(frame.pcm),
                "sample_rate": frame.sample_rate,
                "turn_id": frame.turn_id,
                "is_final": frame.is_final,
            },
        )
    if isinstance(frame, SpeechPresenterFrame):
        return _envelope(
            "speech_presenter",
            {
                "text": frame.text,
                "style": _to_jsonable(frame.style),
                "turn_id": frame.turn_id,
                "chunk_index": frame.chunk_index,
                "is_final": frame.is_final,
            },
        )
    if isinstance(frame, TranscriptionFrame):
        return _envelope(
            "transcription",
            {
                "text": frame.text,
                "is_final": frame.is_final,
                "turn_id": frame.turn_id,
                "lang": frame.lang,
            },
        )
    if isinstance(frame, VisionEventFrame):
        return _envelope(
            "vision_event",
            {"event": frame.event, "payload": _to_jsonable(frame.payload)},
            ts_ms=frame.ts_ms,
        )
    if isinstance(frame, AudioFrame):
        return _envelope(
            "audio_chunk",
            {
                "pcm_b64": _b64(frame.pcm),
                "sample_rate": frame.sample_rate,
                "channels": frame.channels,
            },
            ts_ms=frame.ts_ms,
        )
    if isinstance(frame, SpeechActivityFrame):
        return _envelope(
            "speech_activity",
            {"state": frame.state},
            ts_ms=frame.ts_ms,
        )
    if isinstance(frame, InterruptFrame):
        return _envelope(
            "interrupt",
            {"scope": frame.scope, "turn_id": frame.turn_id, "reason": frame.reason},
            ts_ms=frame.ts_ms,
        )
    if isinstance(frame, TTSStopFrame):
        return _envelope(
            "tts_stop",
            {"turn_id": frame.turn_id, "reason": frame.reason},
        )
    if isinstance(frame, TextFrame):
        return _envelope("text", {"text": frame.text, "turn_id": frame.turn_id})
    if isinstance(frame, TickFrame):
        return _envelope("tick", {"tick_id": frame.tick_id}, ts_ms=frame.ts_ms)
    if isinstance(frame, PipelineErrorFrame):
        return _envelope(
            "pipeline_error",
            {
                "component": frame.component,
                "reason": frame.reason,
                "metadata": _to_jsonable(frame.metadata),
            },
        )
    raise WireSerializationError(
        f"Unsupported outbound frame type: {type(frame).__name__}"
    )


def decode_inbound(message: Mapping[str, Any]) -> Any:
    """Decode a websocket JSON envelope into a v4 inbound frame."""
    if not isinstance(message, Mapping):
        raise WireDecodeError("Inbound message must be a JSON object.")
    type_name = str(message.get("type") or "").strip()
    if not type_name:
        raise WireDecodeError("Inbound message missing 'type'.")
    if _is_legacy_inbound_type(type_name):
        raise WireDecodeError(f"Legacy protocol rejected: {type_name}")
    payload = message.get("payload") or {}
    if not isinstance(payload, Mapping):
        raise WireDecodeError("Inbound 'payload' must be an object.")
    ts_ms = _coerce_ts_ms(message.get("ts_ms"))

    if type_name == "browser_input":
        kind = str(payload.get("kind") or "").strip()
        if not kind:
            raise WireDecodeError("browser_input.kind is required.")
        inner = payload.get("payload") or {}
        if not isinstance(inner, Mapping):
            raise WireDecodeError("browser_input.payload must be an object.")
        session_id = str(payload.get("session_id") or "").strip()
        return BrowserInputFrame(
            kind=kind,
            payload=dict(inner),
            session_id=session_id,
        )
    if type_name == "audio_chunk":
        pcm = _from_b64(payload.get("pcm_b64"))
        sample_rate = int(payload.get("sample_rate") or 0)
        channels = int(payload.get("channels") or 1)
        return AudioFrame(
            pcm=pcm,
            sample_rate=sample_rate,
            channels=channels,
            ts_ms=ts_ms,
        )
    if type_name == "audio_stop":
        return _AudioStop()
    if type_name == "speech_activity":
        state = str(payload.get("state") or "").strip()
        if state not in {"start", "end"}:
            raise WireDecodeError(
                f"speech_activity.state must be start or end, got {state!r}."
            )
        return SpeechActivityFrame(state=state, ts_ms=ts_ms)
    if type_name == "transcription":
        return TranscriptionFrame(
            text=str(payload.get("text") or ""),
            is_final=bool(payload.get("is_final", False)),
            turn_id=str(payload.get("turn_id") or ""),
            lang=str(payload.get("lang") or ""),
        )
    if type_name == "vision_event":
        inner = payload.get("payload") or {}
        if not isinstance(inner, Mapping):
            raise WireDecodeError("vision_event.payload must be an object.")
        return VisionEventFrame(
            event=str(payload.get("event") or ""),
            payload=dict(inner),
            ts_ms=ts_ms,
        )
    if type_name == "camera_frame":
        return _decode_camera_frame(payload, ts_ms=ts_ms)
    if type_name == "ping":
        return _Ping()
    if type_name == "pong":
        return _Pong()
    raise WireDecodeError(f"Unknown inbound type: {type_name}")


class _Ping:
    """Marker for an inbound ping; the ws layer answers with 'pong'."""


class _Pong:
    """Marker for an inbound pong; the ws layer heartbeat consumes it."""


class _AudioStop:
    """Marker for an inbound audio_stop request."""


def _is_legacy_inbound_type(type_name: str) -> bool:
    return type_name in LEGACY_INBOUND_TYPES or type_name.startswith(
        LEGACY_INBOUND_PREFIXES
    )


def _decode_camera_frame(payload: Mapping[str, Any], *, ts_ms: int) -> CameraFrame:
    image_b64 = payload.get("image_b64")
    if not isinstance(image_b64, str) or not image_b64.strip():
        raise WireDecodeError("camera_frame.image_b64 must be a non-empty string.")
    image_b64 = image_b64.strip()
    if len(image_b64) > CAMERA_FRAME_MAX_B64_CHARS:
        raise WireDecodeError("camera_frame.image_b64 is too large.")

    mime_type = str(payload.get("mime_type") or "image/jpeg").strip().lower()
    if mime_type not in CAMERA_FRAME_MIME_TYPES:
        raise WireDecodeError(f"Unsupported camera_frame.mime_type: {mime_type!r}.")

    width = _coerce_camera_dimension(payload.get("width"), "width")
    height = _coerce_camera_dimension(payload.get("height"), "height")
    return CameraFrame(
        image_b64=image_b64,
        mime_type=mime_type,
        width=width,
        height=height,
        ts_ms=ts_ms,
    )


def _coerce_camera_dimension(value: Any, name: str) -> int:
    try:
        dimension = int(value)
    except (TypeError, ValueError) as exc:
        raise WireDecodeError(f"camera_frame.{name} must be an integer.") from exc
    if dimension <= 0 or dimension > CAMERA_FRAME_MAX_DIMENSION:
        raise WireDecodeError(
            f"camera_frame.{name} must be between 1 and {CAMERA_FRAME_MAX_DIMENSION}."
        )
    return dimension


def _envelope(
    type_name: str, payload: dict[str, Any], *, ts_ms: int | None = None
) -> dict[str, Any]:
    envelope: dict[str, Any] = {"type": type_name}
    if ts_ms is not None:
        envelope["ts_ms"] = int(ts_ms)
    envelope["payload"] = payload
    return envelope


def action_result_to_json(result: ActionResult) -> dict[str, Any]:
    """Encode an ActionResult to JSON (used outside ActionResultFrame for completeness)."""
    return {
        "request_id": result.request_id,
        "action_id": result.action_id,
        "name": result.name,
        "owner_id": result.owner_id,
        "status": result.status,
        "result": _to_jsonable(result.result),
        "error": result.error,
        "duration_ms": result.duration_ms,
    }


def _b64(data: bytes) -> str:
    if not isinstance(data, (bytes, bytearray)):
        raise WireSerializationError("PCM payload must be bytes.")
    return base64.b64encode(bytes(data)).decode("ascii")


def _from_b64(value: Any) -> bytes:
    if value is None:
        return b""
    if not isinstance(value, str):
        raise WireDecodeError("pcm_b64 must be a base64 string.")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise WireDecodeError(f"Invalid base64 PCM payload: {exc}") from exc


def _coerce_ts_ms(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise WireDecodeError("ts_ms must be an integer.") from exc


def _metadata_ts(metadata: Mapping[str, Any]) -> int | None:
    ts = metadata.get("ts_ms")
    if ts is None:
        return None
    try:
        return int(ts)
    except (TypeError, ValueError):
        return None


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return _b64(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, PurePath):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_to_jsonable(item) for item in value)
    if is_dataclass(value):
        return {key: _to_jsonable(item) for key, item in value.__dict__.items()}
    if hasattr(value, "__dict__"):
        return {key: _to_jsonable(item) for key, item in value.__dict__.items()}
    raise WireSerializationError(
        f"Cannot serialize value of type {type(value).__name__}"
    )
