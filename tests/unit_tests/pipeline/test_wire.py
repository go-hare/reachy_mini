"""Tests for v4 websocket wire serialization."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdk_fakes import assistant_message  # noqa: E402

from reachy_mini.action_runtime import ActionResult  # noqa: E402
from reachy_mini.pipeline.frames import (
    ActionResultFrame,
    AudioFrame,
    BrowserInputFrame,
    CameraFrame,
    EmbodimentFrame,
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
from reachy_mini.pipeline.wire import (
    CAMERA_FRAME_MAX_B64_CHARS,
    CAMERA_FRAME_MAX_DIMENSION,
    LEGACY_INBOUND_TYPES,
    WireDecodeError,
    WireSerializationError,
    _AudioStop,
    _Ping,
    _Pong,
    decode_inbound,
    encode_frame,
)
from reachy_mini.robot_runtime import RobotEvent  # noqa: E402


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_encode_sdk_message_preserves_sdk_payload_and_metadata() -> None:
    """SDKMessageFrame encodes SDK-native messages, turn id, and metadata."""
    frame = SDKMessageFrame(
        message=assistant_message("hi", session_id="T1"),
        turn_id="T1",
        metadata={"latency_ms": 12, "ts_ms": 1717},
    )

    envelope = encode_frame(frame)

    assert envelope["type"] == "sdk_message"
    assert envelope["ts_ms"] == 1717
    assert envelope["payload"]["message_type"] == "AssistantMessage"
    assert envelope["payload"]["content"][0]["text"] == "hi"
    assert envelope["payload"]["turn_id"] == "T1"
    assert envelope["payload"]["metadata"]["latency_ms"] == 12


def test_encode_action_result_frame() -> None:
    """ActionResultFrame encodes round-trippable payloads."""
    result = ActionResult(
        request_id="r3",
        action_id="a3",
        name="nod",
        owner_id="main-agent",
        status="ok",
        duration_ms=42,
    )
    result_envelope = encode_frame(ActionResultFrame.from_result(result))
    assert result_envelope["type"] == "action_result"
    assert result_envelope["payload"] == {
        "request_id": "r3",
        "name": "nod",
        "owner_id": "main-agent",
        "status": "ok",
        "result": None,
        "error": None,
        "duration_ms": 42,
    }


def test_encode_audio_and_tts_payloads_use_b64() -> None:
    """AudioFrame and TTSAudioFrame encode PCM as base64."""
    pcm = bytes([1, 2, 3, 4])
    audio = encode_frame(AudioFrame(pcm=pcm, sample_rate=16000, channels=1, ts_ms=500))
    assert audio["type"] == "audio_chunk"
    assert audio["ts_ms"] == 500
    assert audio["payload"]["pcm_b64"] == _b64(pcm)

    tts = encode_frame(
        TTSAudioFrame(pcm=pcm, sample_rate=24000, turn_id="T1", is_final=True)
    )
    assert tts["type"] == "tts_audio"
    assert tts["payload"]["pcm_b64"] == _b64(pcm)
    assert tts["payload"]["is_final"] is True


def test_encode_supporting_frame_types() -> None:
    """Smaller frames serialize with their canonical type name."""
    presenter = encode_frame(
        SpeechPresenterFrame(
            text="x", style={}, turn_id="T", chunk_index=0, is_final=True
        )
    )
    assert presenter["type"] == "speech_presenter"

    transcript = encode_frame(
        TranscriptionFrame(text="hello", is_final=False, turn_id="T", lang="zh")
    )
    assert transcript["type"] == "transcription"
    assert transcript["payload"] == {
        "text": "hello",
        "is_final": False,
        "turn_id": "T",
        "lang": "zh",
    }

    vision = encode_frame(
        VisionEventFrame(event="face_detected", payload={"id": "f1"}, ts_ms=99)
    )
    assert vision["type"] == "vision_event"
    assert vision["ts_ms"] == 99

    worker = encode_frame(
        WorkerEventFrame(
            task_id="w1", event="progress", payload={"note": "x"}, ts_ms=10
        )
    )
    assert worker["type"] == "worker_event"
    assert worker["payload"]["payload"]["note"] == "x"

    text = encode_frame(TextFrame(text="t", turn_id="T"))
    assert text["type"] == "text"

    tick = encode_frame(TickFrame(ts_ms=42, tick_id=7))
    assert tick["type"] == "tick"
    assert tick["ts_ms"] == 42

    interrupt = encode_frame(
        InterruptFrame(scope="speech", turn_id="T", reason="barge_in", ts_ms=11)
    )
    assert interrupt["type"] == "interrupt"

    stop = encode_frame(TTSStopFrame(turn_id="T", reason="x"))
    assert stop["type"] == "tts_stop"

    error = encode_frame(PipelineErrorFrame(component="stt", reason="boom"))
    assert error["type"] == "pipeline_error"
    assert error["payload"]["reason"] == "boom"


def test_encode_embodiment_frame() -> None:
    """EmbodimentFrame serializes body-agnostic expression events."""
    envelope = encode_frame(
        EmbodimentFrame(
            action="surface_state",
            target="avatar",
            turn_id="T1",
            payload={"phase": "replying", "pose": "speak"},
            ts_ms=1234,
        )
    )

    assert envelope == {
        "type": "embodiment",
        "ts_ms": 1234,
        "payload": {
            "action": "surface_state",
            "target": "avatar",
            "turn_id": "T1",
            "payload": {"phase": "replying", "pose": "speak"},
        },
    }


def test_encode_robot_event() -> None:
    """RobotEvent serializes as a traceable robot_event envelope."""
    envelope = encode_frame(
        RobotEvent(
            event_id="event_1",
            source="runtime",
            event_type="intent_received",
            ts_ms=777,
            turn_id="T1",
            intent_id="intent_1",
            payload={"intent_type": "greet"},
        )
    )

    assert envelope == {
        "type": "robot_event",
        "ts_ms": 777,
        "payload": {
            "event_id": "event_1",
            "ts_ms": 777,
            "source": "runtime",
            "severity": "info",
            "event_type": "intent_received",
            "turn_id": "T1",
            "intent_id": "intent_1",
            "plan_id": None,
            "command_id": None,
            "status": None,
            "payload": {"intent_type": "greet"},
            "reason": None,
        },
    }


def test_encode_unsupported_frame_raises() -> None:
    """Unknown outbound types raise WireSerializationError."""
    with pytest.raises(WireSerializationError):
        encode_frame(object())


def test_decode_browser_input_text() -> None:
    """browser_input(kind=text) decodes to BrowserInputFrame."""
    frame = decode_inbound(
        {
            "type": "browser_input",
            "ts_ms": 100,
            "payload": {
                "kind": "text",
                "session_id": "s1",
                "payload": {"text": "hi", "turn_id": "T1"},
            },
        }
    )
    assert isinstance(frame, BrowserInputFrame)
    assert frame.kind == "text"
    assert frame.session_id == "s1"
    assert frame.payload == {"text": "hi", "turn_id": "T1"}


def test_decode_audio_chunk_round_trips_b64() -> None:
    """audio_chunk decodes pcm_b64 back to bytes."""
    pcm = bytes([5, 6, 7])
    frame = decode_inbound(
        {
            "type": "audio_chunk",
            "ts_ms": 250,
            "payload": {
                "pcm_b64": _b64(pcm),
                "sample_rate": 16000,
                "channels": 1,
            },
        }
    )
    assert isinstance(frame, AudioFrame)
    assert frame.pcm == pcm
    assert frame.sample_rate == 16000
    assert frame.ts_ms == 250


def test_decode_camera_frame_for_browser_fed_vision() -> None:
    """camera_frame decodes browser-provided JPEG frames for local YOLO."""
    frame = decode_inbound(
        {
            "type": "camera_frame",
            "ts_ms": 300,
            "payload": {
                "image_b64": "abcd",
                "mime_type": "image/jpeg",
                "width": 320,
                "height": 180,
            },
        }
    )

    assert isinstance(frame, CameraFrame)
    assert frame.image_b64 == "abcd"
    assert frame.mime_type == "image/jpeg"
    assert frame.width == 320
    assert frame.height == 180
    assert frame.ts_ms == 300


@pytest.mark.parametrize(
    ("payload_update", "reason"),
    [
        ({"image_b64": ""}, "non-empty"),
        ({"image_b64": "a" * (CAMERA_FRAME_MAX_B64_CHARS + 1)}, "too large"),
        ({"mime_type": "image/png"}, "Unsupported"),
        ({"width": 0}, "width"),
        ({"height": CAMERA_FRAME_MAX_DIMENSION + 1}, "height"),
    ],
)
def test_decode_camera_frame_validates_browser_payload(
    payload_update: dict[str, object],
    reason: str,
) -> None:
    """camera_frame rejects oversized or unsupported browser image payloads."""
    payload: dict[str, object] = {
        "image_b64": "abcd",
        "mime_type": "image/jpeg",
        "width": 320,
        "height": 180,
    }
    payload.update(payload_update)

    with pytest.raises(WireDecodeError, match=reason):
        decode_inbound({"type": "camera_frame", "ts_ms": 300, "payload": payload})


def test_decode_speech_activity_validates_state() -> None:
    """speech_activity rejects unknown states."""
    frame = decode_inbound(
        {"type": "speech_activity", "ts_ms": 1, "payload": {"state": "start"}}
    )
    assert isinstance(frame, SpeechActivityFrame)
    assert frame.state == "start"

    with pytest.raises(WireDecodeError):
        decode_inbound(
            {"type": "speech_activity", "ts_ms": 1, "payload": {"state": "weird"}}
        )


def test_decode_ping_and_audio_stop_are_markers() -> None:
    """ping/audio_stop decode to internal marker objects."""
    assert isinstance(decode_inbound({"type": "ping", "payload": {}}), _Ping)
    assert isinstance(decode_inbound({"type": "pong", "payload": {}}), _Pong)
    assert isinstance(decode_inbound({"type": "audio_stop", "payload": {}}), _AudioStop)


def test_decode_legacy_inbound_types_raise() -> None:
    """Every legacy front_* and browser_* inbound type is rejected."""
    for legacy in LEGACY_INBOUND_TYPES:
        with pytest.raises(WireDecodeError, match="Legacy"):
            decode_inbound({"type": legacy, "payload": {}})


def test_decode_unknown_type_raises() -> None:
    """Unknown inbound type raises WireDecodeError."""
    with pytest.raises(WireDecodeError):
        decode_inbound({"type": "weird", "payload": {}})


def test_decode_missing_type_raises() -> None:
    """Inbound message without 'type' raises WireDecodeError."""
    with pytest.raises(WireDecodeError):
        decode_inbound({"payload": {}})
