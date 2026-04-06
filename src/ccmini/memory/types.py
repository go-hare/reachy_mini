"""Pydantic models for the 3-layer memory system.

Ported from reachy_mini.core.memory — structurally identical,
decoupled from LangChain.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .utils import now_iso


class MemoryCandidate(BaseModel):
    memory_id: str = ""
    memory_type: Literal["user", "feedback", "project", "reference"] = "project"
    summary: str = ""
    detail: str = ""
    confidence: float = 0.0
    stability: float = 0.0
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LongTermRecord(BaseModel):
    record_id: str = ""
    user_id: str = ""
    agent_id: str = ""
    conversation_id: str = ""
    turn_id: str = ""
    summary: str = ""
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
    user_updates: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)


class CognitiveEvent(BaseModel):
    event_id: str = ""
    user_id: str = ""
    agent_id: str = ""
    conversation_id: str = ""
    turn_id: str = ""
    summary: str = ""
    outcome: str = "unknown"
    reason: str = ""
    needs_deep_reflection: bool = False
    user_text: str = ""
    assistant_text: str = ""
    source_event_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)


class MemoryPatch(BaseModel):
    cognitive_append: list[CognitiveEvent] = Field(default_factory=list)
    long_term_append: list[LongTermRecord] = Field(default_factory=list)
    user_updates: list[str] = Field(default_factory=list)


class MemoryView(BaseModel):
    raw_layer: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    cognitive_layer: list[dict[str, Any]] = Field(default_factory=list)
    long_term_layer: dict[str, Any] = Field(default_factory=dict)
    projections: dict[str, str] = Field(default_factory=dict)
