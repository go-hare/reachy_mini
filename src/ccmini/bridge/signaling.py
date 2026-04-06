"""Typed signaling payloads for WebRTC bridge negotiation."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SignalingRole(str, Enum):
    CLIENT = "client"
    EXECUTOR = "executor"


class SignalingKind(str, Enum):
    OFFER = "offer"
    ANSWER = "answer"
    ICE_CANDIDATE = "ice_candidate"
    BYE = "bye"


class SignalingAction(str, Enum):
    PUBLISH = "publish"
    FETCH = "fetch"


@dataclass(slots=True)
class SignalingMessage:
    """A single WebRTC signaling item stored by the bridge."""

    session_id: str
    sender: SignalingRole
    recipient: SignalingRole
    kind: SignalingKind
    data: dict[str, Any] = field(default_factory=dict)
    sequence_num: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sender"] = self.sender.value
        data["recipient"] = self.recipient.value
        data["kind"] = self.kind.value
        return data


def parse_signaling_role(value: str) -> SignalingRole:
    return SignalingRole(str(value).strip().lower())


def parse_signaling_kind(value: str) -> SignalingKind:
    return SignalingKind(str(value).strip().lower())
