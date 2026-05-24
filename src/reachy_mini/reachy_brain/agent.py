"""Minimal v4 Brain agent facade for Phase 1."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from reachy_mini.action_runtime import ActionSpec
from reachy_mini.action_runtime.registry import ActionRegistry

from .config import AgentConfig
from .tool_adapter import ToolCall, tool_call_to_action_spec


@dataclass(frozen=True)
class BrainTurnInput:
    """Input supplied to the Brain for one completed turn."""

    text: str
    turn_id: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrainTurnOutput:
    """Structured Brain output consumed by the pipeline layer."""

    reply_text: str
    speech_style: dict[str, Any]
    actions: list[ActionSpec]
    worker_decision: dict[str, Any] | None
    raw_model_response: dict[str, Any]


class BrainModel(Protocol):
    """Protocol for deterministic mock or real model backends."""

    async def run(
        self,
        turn_input: BrainTurnInput,
        *,
        config: AgentConfig,
        registry: ActionRegistry,
    ) -> dict[str, Any]:
        """Return a normalized model response dictionary."""
        ...


class MockBrainModel:
    """Deterministic model used by Phase 1 tests and mock runner mode."""

    async def run(
        self,
        turn_input: BrainTurnInput,
        *,
        config: AgentConfig,
        registry: ActionRegistry,
    ) -> dict[str, Any]:
        """Produce simple reply/action/worker decisions from text keywords."""
        text = turn_input.text.strip()
        normalized = text.lower()
        if any(keyword in normalized for keyword in ("巡视", "patrol")):
            task_id = f"patrol_{uuid.uuid4().hex[:8]}"
            return {
                "reply_text": "我开始巡视，过程中你仍然可以继续和我说话。",
                "speech_style": {"voice": config.speech.voice},
                "tool_calls": [],
                "worker_decision": {
                    "op": "spawn",
                    "task_id": task_id,
                    "task_type": "patrol",
                    "task_params": {"rounds": 1},
                    "summary": "start patrol worker",
                },
            }
        if any(keyword in normalized for keyword in ("几点", "time")):
            return {
                "reply_text": "现在是 mock 时间。",
                "speech_style": {"voice": config.speech.voice},
                "tool_calls": [],
                "worker_decision": None,
            }
        if any(keyword in normalized for keyword in ("摇头", "不", "no")):
            return {
                "reply_text": "好的。",
                "speech_style": {"voice": config.speech.voice},
                "tool_calls": [
                    {"name": "shake_head", "arguments": {"cycles": 1}, "reason": "negative response"}
                ],
                "worker_decision": None,
            }
        action_name = "nod" if registry.get_metadata("nod") else ""
        return {
            "reply_text": f"我听到了：{text}" if text else "我在。",
            "speech_style": {"voice": config.speech.voice},
            "tool_calls": [
                {"name": action_name, "arguments": {"cycles": 1}, "reason": "short acknowledgement"}
            ]
            if action_name
            else [],
            "worker_decision": None,
        }


class BrainAgent:
    """Single Brain loop facade that outputs action intent only."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        registry: ActionRegistry,
        model: BrainModel | None = None,
        owner_id: str = "main-agent",
    ) -> None:
        """Create a Brain agent bound to action metadata."""
        self.config = config
        self.registry = registry
        self.model = model or MockBrainModel()
        self.owner_id = owner_id

    async def run(self, turn_input: BrainTurnInput) -> BrainTurnOutput:
        """Run one Brain turn and return a structured output."""
        request_id = f"brain_{uuid.uuid4().hex}"
        raw = await self.model.run(
            turn_input,
            config=self.config,
            registry=self.registry,
        )
        actions = [
            tool_call_to_action_spec(
                ToolCall(
                    name=str(item.get("name", "")),
                    arguments=dict(item.get("arguments", {}) or {}),
                    reason=str(item.get("reason", "") or ""),
                ),
                registry=self.registry,
                request_id=request_id,
                owner_id=self.owner_id,
            )
            for item in raw.get("tool_calls", [])
        ]
        return BrainTurnOutput(
            reply_text=str(raw.get("reply_text", "") or ""),
            speech_style=dict(raw.get("speech_style", {}) or {}),
            actions=actions,
            worker_decision=raw.get("worker_decision"),
            raw_model_response=raw,
        )
