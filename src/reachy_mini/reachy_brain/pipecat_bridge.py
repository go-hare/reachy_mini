"""Bridge helpers from Claude Agent SDK messages to pipeline frames."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from reachy_mini.pipeline.frames import (
    SDKMessageFrame,
    SpeechPresenterFrame,
    WorkerEventFrame,
)


def sdk_message_to_speech_frames(
    frame: SDKMessageFrame,
    *,
    style: dict[str, Any] | None = None,
) -> list[SpeechPresenterFrame]:
    """Convert SDK AssistantMessage/TextBlock content to TTS text frames."""
    chunks = extract_text_blocks(frame.message)
    return [
        SpeechPresenterFrame(
            text=text,
            style=dict(style or {}),
            turn_id=frame.turn_id,
            chunk_index=index,
            is_final=index == len(chunks) - 1,
        )
        for index, text in enumerate(chunks)
    ]


def sdk_message_to_worker_event(frame: SDKMessageFrame) -> WorkerEventFrame | None:
    """Convert SDK task/subagent messages to an observable worker event frame."""
    message = frame.message
    name = type(message).__name__
    if name == "TaskStartedMessage":
        event = "started"
    elif name == "TaskProgressMessage":
        event = "progress"
    elif name == "TaskNotificationMessage":
        event = str(getattr(message, "status", "") or "notification")
    else:
        return None
    payload = sdk_message_to_payload(message)
    return WorkerEventFrame(
        task_id=str(getattr(message, "task_id", "") or ""),
        event=event,
        payload=payload,
        ts_ms=0,
    )


def extract_text_blocks(message: Any) -> list[str]:
    """Extract text from SDK AssistantMessage/TextBlock content."""
    if type(message).__name__ != "AssistantMessage":
        return []
    content = getattr(message, "content", [])
    texts: list[str] = []
    for block in content:
        if type(block).__name__ == "TextBlock":
            text = str(getattr(block, "text", "") or "").strip()
            if text:
                texts.append(text)
    return texts


def sdk_message_to_payload(message: Any) -> dict[str, Any]:
    """Serialize an SDK message to a JSON-friendly payload preserving SDK fields."""
    payload = _to_jsonable(message)
    if isinstance(payload, dict):
        payload.setdefault("message_type", type(message).__name__)
        return payload
    return {"message_type": type(message).__name__, "value": payload}


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_to_jsonable(item) for item in value]
    if is_dataclass(value):
        return {key: _to_jsonable(item) for key, item in asdict(value).items()}
    if hasattr(value, "__dict__"):
        return {key: _to_jsonable(item) for key, item in value.__dict__.items()}
    return str(value)
