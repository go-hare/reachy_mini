"""Lightweight front-layer expressive event models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class FrontSignal:
    """One expressive lifecycle signal delivered into the front layer."""

    name: str
    thread_id: str
    turn_id: str = ""
    user_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class FrontToolCall:
    """One front-owned expressive tool call intention."""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(slots=True, frozen=True)
class FrontDecision:
    """One front-side expressive decision derived from a signal."""

    signal_name: str
    thread_id: str
    turn_id: str = ""
    reply_text: str = ""
    lifecycle_state: str = ""
    surface_patch: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[FrontToolCall] = field(default_factory=list)
    debug_reason: str = ""


FrontSignalResult = FrontDecision
