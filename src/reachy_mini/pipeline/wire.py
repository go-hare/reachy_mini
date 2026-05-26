"""Frame ↔ websocket JSON serialization for the v4 pipeline."""

from __future__ import annotations

import base64
import binascii
from dataclasses import is_dataclass
from enum import Enum
from pathlib import PurePath
from typing import Any, Mapping

from reachy_mini.action_runtime import ActionResult, ActionSpec

from .frames import (
    ActionResultFrame,
    ActionSpecFrame,
    AudioFrame,
    BrainReplyFrame,
    BrowserInputFrame,
    InterruptFrame,
    PipelineErrorFrame,
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


LEGACY_INBOUND_TYPES = frozenset(
    {
        "front_hint_chunk",
        "front_hint_done",
        "front_final_chunk",
        "front_final_done",
        "front_decision",
        "front_tool_result",
        "user_text",
        "browser_audio_chunk",
        "browser_audio_stop",
    }
)


class WireSerializationError(ValueError):
    """Raised when an outbound frame cannot be encoded to JSON."""


class WireDecodeError(ValueError):
    """Raised when an inbound websocket message cannot be decoded."""


def encode_frame(frame: Any) -> dict[str, Any]:
    """Encode a v4 outbound frame as a websocket JSON envelope."""
    if isinstance(frame, BrainReplyFrame):
        payload = {
            "reply_text": frame.reply_text,
            "speech_style": _to_jsonable(frame.speech_style),
            "actions": [_action_spec_to_json(spec) for spec in frame.actions],
            "worker_decision": _to_jsonable(frame.worker_decision),
            "turn_id": frame.turn_id,
            "request_id": frame.request_id,
            "is_final": frame.is_final,
            "metadata": _to_jsonable(frame.metadata),
        }
        return _envelope("brain_reply", payload, ts_ms=_metadata_ts(frame))
    if isinstance(frame, ActionSpecFrame):
        return _envelope(
            "action_spec",
            {"spec": _action_spec_to_json(frame.spec), "turn_id": frame.turn_id},
        )
    if isinstance(frame, ActionResultFrame):
        return _envelope(
            "action_result",
            {
                "request_id": frame.request_id,
                "name": frame.name,
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
    raise WireSerializationError(f"Unsupported outbound frame type: {type(frame).__name__}")


def decode_inbound(message: Mapping[str, Any]) -> Any:
    """Decode a websocket JSON envelope into a v4 inbound frame."""
    if not isinstance(message, Mapping):
        raise WireDecodeError("Inbound message must be a JSON object.")
    type_name = str(message.get("type") or "").strip()
    if not type_name:
        raise WireDecodeError("Inbound message missing 'type'.")
    if type_name in LEGACY_INBOUND_TYPES:
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
            raise WireDecodeError(f"speech_activity.state must be start or end, got {state!r}.")
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
    if type_name == "ping":
        return _Ping()
    raise WireDecodeError(f"Unknown inbound type: {type_name}")


class _Ping:
    """Marker for an inbound ping; the ws layer answers with 'pong'."""


class _AudioStop:
    """Marker for an inbound audio_stop request."""


def _envelope(type_name: str, payload: dict[str, Any], *, ts_ms: int | None = None) -> dict[str, Any]:
    envelope: dict[str, Any] = {"type": type_name}
    if ts_ms is not None:
        envelope["ts_ms"] = int(ts_ms)
    envelope["payload"] = payload
    return envelope


def _action_spec_to_json(spec: ActionSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "params": _to_jsonable(spec.params),
        "reason": spec.reason,
        "request_id": spec.request_id,
        "owner_id": spec.owner_id,
        "priority": spec.priority,
        "interruptible": spec.interruptible,
        "deadline_s": spec.deadline_s,
        "parent_request_id": spec.parent_request_id,
    }


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


def _metadata_ts(frame: BrainReplyFrame) -> int | None:
    ts = frame.metadata.get("ts_ms") if isinstance(frame.metadata, Mapping) else None
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
    if isinstance(value, ActionSpec):
        return _action_spec_to_json(value)
    if is_dataclass(value):
        return {key: _to_jsonable(item) for key, item in value.__dict__.items()}
    if hasattr(value, "__dict__"):
        return {key: _to_jsonable(item) for key, item in value.__dict__.items()}
    raise WireSerializationError(f"Cannot serialize value of type {type(value).__name__}")
