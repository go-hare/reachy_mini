"""Bridge message protocol — typed messages with JSON serialization.

Ported from Claude Code's ``bridge/bridgeMessaging.ts``:
- ``BridgeMessage`` dataclass with type, payload, session, timestamp
- ``MessageType`` enum covering the full lifecycle (QUERY → RESPONSE, etc.)
- ``encode`` / ``decode`` for wire-safe JSON
- ``validate_message`` for schema checks before dispatch
"""

from __future__ import annotations

import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Message types ───────────────────────────────────────────────────

class MessageType(enum.Enum):
    """Wire-level message type discriminant."""

    QUERY = "query"
    RESPONSE = "response"
    TOOL_CALL = "tool_call"
    SUBMIT_TOOL_RESULTS = "submit_tool_results"
    TOOL_RESULT = "tool_result"
    STATUS = "status"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    EVENTS = "events"
    SIGNALING = "signaling"


_VALID_TYPES = frozenset(t.value for t in MessageType)


# ── Message dataclass ───────────────────────────────────────────────

@dataclass(slots=True)
class BridgeMessage:
    """A single message on the bridge transport layer."""

    type: MessageType
    payload: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    timestamp: float = 0.0
    request_id: str = ""
    sequence_num: int = 0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()
        if not self.request_id:
            self.request_id = uuid.uuid4().hex[:12]


# ── Serialization ───────────────────────────────────────────────────

def encode(msg: BridgeMessage) -> str:
    """Serialize a ``BridgeMessage`` to a JSON string."""
    return json.dumps(
        {
            "type": msg.type.value,
            "payload": msg.payload,
            "session_id": msg.session_id,
            "timestamp": msg.timestamp,
            "request_id": msg.request_id,
            "sequence_num": msg.sequence_num,
        },
        ensure_ascii=False,
    )


def decode(data: str | bytes) -> BridgeMessage:
    """Deserialize a JSON string into a ``BridgeMessage``.

    Raises ``ValueError`` if the data is not valid JSON or the ``type``
    field is unrecognised.
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8")

    raw = json.loads(data)

    type_str = raw.get("type", "")
    if type_str not in _VALID_TYPES:
        raise ValueError(f"Unknown message type: {type_str!r}")

    return BridgeMessage(
        type=MessageType(type_str),
        payload=raw.get("payload", {}),
        session_id=raw.get("session_id", ""),
        timestamp=raw.get("timestamp", 0.0),
        request_id=raw.get("request_id", ""),
        sequence_num=int(raw.get("sequence_num", 0) or 0),
    )


# ── Validation ──────────────────────────────────────────────────────

_REQUIRED_FIELDS = frozenset({"type"})

_PAYLOAD_SCHEMAS: dict[MessageType, set[str]] = {
    MessageType.QUERY: {"text"},
    MessageType.RESPONSE: {"text"},
    MessageType.TOOL_CALL: {"tool_name", "tool_input"},
    MessageType.SUBMIT_TOOL_RESULTS: {"run_id", "results"},
    MessageType.TOOL_RESULT: {"tool_name", "result"},
    MessageType.STATUS: {"status"},
    MessageType.ERROR: {"error"},
    MessageType.HEARTBEAT: set(),
    MessageType.EVENTS: {"events"},
    MessageType.SIGNALING: {"action"},
}


def validate_message(msg: BridgeMessage) -> list[str]:
    """Validate a ``BridgeMessage`` against its type's schema.

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    if not msg.type:
        errors.append("Missing required field: type")
        return errors

    expected_keys = _PAYLOAD_SCHEMAS.get(msg.type, set())
    for key in expected_keys:
        if key not in msg.payload:
            errors.append(f"Payload missing required key for {msg.type.value}: {key}")

    if not msg.session_id and msg.type not in (MessageType.HEARTBEAT, MessageType.STATUS):
        errors.append("session_id required for non-heartbeat messages")

    return errors


# ── Convenience factories ───────────────────────────────────────────

def make_query(session_id: str, text: str) -> BridgeMessage:
    return BridgeMessage(
        type=MessageType.QUERY,
        payload={"text": text},
        session_id=session_id,
    )


def make_response(session_id: str, text: str, *, request_id: str = "") -> BridgeMessage:
    return BridgeMessage(
        type=MessageType.RESPONSE,
        payload={"text": text},
        session_id=session_id,
        request_id=request_id,
    )


def make_error(session_id: str, error: str, *, request_id: str = "") -> BridgeMessage:
    return BridgeMessage(
        type=MessageType.ERROR,
        payload={"error": error},
        session_id=session_id,
        request_id=request_id,
    )


def make_heartbeat() -> BridgeMessage:
    return BridgeMessage(type=MessageType.HEARTBEAT)


def make_events(
    session_id: str,
    events: list[dict[str, Any]],
    *,
    request_id: str = "",
) -> BridgeMessage:
    return BridgeMessage(
        type=MessageType.EVENTS,
        payload={"events": events},
        session_id=session_id,
        request_id=request_id,
    )
