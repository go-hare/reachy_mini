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
class FrontToolExecution:
    """One executed front-tool result."""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    success: bool = False
    result: str = ""


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


@dataclass(slots=True, frozen=True)
class FrontUserTurnResult:
    """One complete front-owned user-turn result."""

    reply_text: str = ""
    tool_calls: list[FrontToolCall] = field(default_factory=list)
    tool_results: list[FrontToolExecution] = field(default_factory=list)
    completes_turn: bool = False
    debug_reason: str = ""


FrontSignalResult = FrontDecision
