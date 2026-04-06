"""Letta-style memory blocks and CoreMemory rendering."""

from __future__ import annotations

from io import StringIO
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .types import MemoryView

CORE_MEMORY_BLOCK_CHAR_LIMIT: int = 100_000
INT32_MAX = 2_147_483_647

DEFAULT_PERSONA_BLOCK_DESCRIPTION = (
    "The persona block: Stores details about your current persona, guiding how you behave and respond."
)
DEFAULT_HUMAN_BLOCK_DESCRIPTION = (
    "The human block: Stores key details about the person you are conversing with."
)


class Block(BaseModel, validate_assignment=True):
    """A reserved section of the context window."""

    id: str = Field(default="")
    value: str = Field(..., description="Value of the block.")
    limit: int = Field(CORE_MEMORY_BLOCK_CHAR_LIMIT, description="Character limit.")
    label: str = Field(..., description="Label of the block.")
    description: str = Field(default="", description="Description of the block.")
    read_only: bool = Field(False)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")

    @field_validator("limit", mode="after")
    @classmethod
    def validate_limit_int32(cls, value: int) -> int:
        if value > INT32_MAX:
            raise ValueError(f"limit must be <= {INT32_MAX}")
        return value

    @field_validator("value", mode="before")
    @classmethod
    def sanitize_value_null_bytes(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.replace("\x00", "")
        return value

    @model_validator(mode="before")
    @classmethod
    def verify_char_limit(cls, data: Any) -> Any:
        if isinstance(data, dict):
            limit = data.get("limit")
            value = data.get("value")
            if limit is not None and value is not None and isinstance(value, str) and len(value) > limit:
                raise ValueError(f"Exceeds {limit} character limit (requested {len(value)})")
        return data


class Human(Block):
    label: str = "human"
    description: str = DEFAULT_HUMAN_BLOCK_DESCRIPTION


class Memory(BaseModel, validate_assignment=True):
    """Minimal Letta-style memory with labelled blocks."""

    blocks: list[Block] = Field(default_factory=list)

    def render(self) -> str:
        handle = StringIO()
        if not self.blocks:
            return ""
        handle.write("<memory_blocks>\n")
        handle.write("The following memory blocks are currently engaged in your core memory unit:\n\n")
        for index, block in enumerate(self.blocks):
            chars_current = len(block.value or "")
            handle.write(f"<{block.label}>\n")
            handle.write(f"<description>\n{block.description}\n</description>\n")
            handle.write("<metadata>")
            if block.read_only:
                handle.write("\n- read_only=true")
            handle.write(f"\n- chars_current={chars_current}")
            handle.write(f"\n- chars_limit={block.limit}\n")
            handle.write("</metadata>\n")
            handle.write(f"<value>\n{block.value}\n</value>\n")
            handle.write(f"</{block.label}>\n")
            if index != len(self.blocks) - 1:
                handle.write("\n")
        handle.write("\n</memory_blocks>")
        return handle.getvalue()

    def get_block(self, label: str) -> Block:
        for block in self.blocks:
            if block.label == label:
                return block
        keys = [b.label for b in self.blocks]
        raise KeyError(f"Block '{label}' not found (available: {', '.join(keys)})")

    def list_block_labels(self) -> list[str]:
        return [b.label for b in self.blocks]


class CoreMemory(Memory):
    """Assembled from MemoryView — renders all 3 layers as blocks."""

    @classmethod
    def from_memory_view(cls, view: MemoryView) -> CoreMemory:
        blocks: list[Block] = []
        user_anchor = str(view.projections.get("user_anchor", "") or "").strip()
        long_term_summary = str(view.long_term_layer.get("summary", "") or "").strip()
        cognitive_summary = cls._render_cognitive(view.cognitive_layer)
        recent_dialogue = cls._render_rows(view.raw_layer.get("recent_dialogue", []), key="role")
        recent_tools = cls._render_rows(view.raw_layer.get("recent_tools", []), key="tool_name")

        if user_anchor:
            blocks.append(Human(value=user_anchor))
        if cognitive_summary:
            blocks.append(Block(label="cognitive_memory", value=cognitive_summary, description="Recent cognitive summaries"))
        if long_term_summary:
            blocks.append(Block(label="summary_memory", value=long_term_summary, description="Retrieved long-term memory"))
        if recent_dialogue:
            blocks.append(Block(label="recent_dialogue", value=recent_dialogue, description="Recent conversation context"))
        if recent_tools:
            blocks.append(Block(label="recent_tools", value=recent_tools, description="Recent tool results"))

        return cls(blocks=blocks)

    @staticmethod
    def _render_rows(rows: list[dict[str, object]], key: str) -> str:
        lines: list[str] = []
        for row in rows[-6:]:
            label = str(row.get(key, "") or "").strip() or "unknown"
            content = str(row.get("content", "") or "").strip()
            if content:
                lines.append(f"{label}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _render_cognitive(rows: list[dict[str, object]]) -> str:
        lines: list[str] = []
        for row in rows[-6:]:
            summary = str(row.get("summary", "") or "").strip()
            outcome = str(row.get("outcome", "") or "").strip()
            if summary and outcome:
                lines.append(f"- [{outcome}] {summary}")
            elif summary:
                lines.append(f"- {summary}")
        return "\n".join(lines)
