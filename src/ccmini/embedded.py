"""Public host-facing types for embeddable ccmini runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class HostEvent:
    """A host/runtime event injected into the agent conversation."""

    conversation_id: str = ""
    turn_id: str = ""
    event_type: str = ""
    role: Literal["system", "user", "assistant"] = "system"
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[Any] = field(default_factory=list)


@dataclass(slots=True)
class HostToolResult:
    """Structured host-side result for a pending client tool call."""

    tool_use_id: str
    text: str = ""
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[Any] = field(default_factory=list)

