"""MemoryAdapter: memory injection for the query loop.

Route 1: Projections (USER.md) → user context meta message
Route 2: Memory summary → system prompt dynamic section
Route 3: Relevant memories → attachments
Route 4: Full CoreMemory block rendering
"""

from __future__ import annotations

from typing import Any

from .blocks import CoreMemory
from .consolidation import ConsolidationAgent
from .store import JsonlMemoryStore
from .types import CognitiveEvent, MemoryPatch
from .utils import make_id, read_text


class MemoryAdapter:
    """Adapt the 3-layer JsonlMemoryStore to the query loop's injection points."""

    def __init__(
        self,
        store: JsonlMemoryStore,
        *,
        consolidation_agent: ConsolidationAgent | None = None,
    ) -> None:
        self.store = store
        self.consolidation_agent = consolidation_agent

    # ------------------------------------------------------------------
    # Route 1: Projections → user context (prepended as meta message)
    # ------------------------------------------------------------------

    def build_user_context(self, conversation_id: str) -> str | None:
        """USER.md content for injection as a user meta message."""
        parts: list[str] = []
        user = read_text(self.store.profile_root / "USER.md").strip()
        if user:
            parts.append(f"# User Profile\n{user}")
        return "\n\n".join(parts) if parts else None

    # ------------------------------------------------------------------
    # Route 2: Memory summary → system prompt dynamic section
    # ------------------------------------------------------------------

    def build_memory_section(self, conversation_id: str, query: str) -> str | None:
        """Long-term summary + cognitive events for the system prompt."""
        view = self.store.build_memory_view(conversation_id, "agent", query)
        sections: list[str] = []

        cognitive = self._render_cognitive(view.cognitive_layer)
        if cognitive:
            sections.append(f"### Recent Cognitive Events\n{cognitive}")

        long_term = str(view.long_term_layer.get("summary", "") or "").strip()
        if long_term:
            sections.append(f"### Long-term Memory\n{long_term}")

        return "## Memory\n" + "\n\n".join(sections) if sections else None

    # ------------------------------------------------------------------
    # Route 3: Relevant memory search → attachments
    # ------------------------------------------------------------------

    def find_relevant_memories(self, conversation_id: str, query: str) -> list[dict[str, Any]]:
        """Retrieve relevant long-term memories as attachment dicts."""
        candidates = self.store.query_long_term(query, "agent", limit=5)
        return [
            {"type": "memory", "content": c.get("summary", ""), "metadata": c}
            for c in candidates
            if c.get("summary")
        ]

    # ------------------------------------------------------------------
    # Route 4: Full CoreMemory blocks
    # ------------------------------------------------------------------

    def build_core_memory_blocks(self, conversation_id: str, query: str) -> str | None:
        """Full Letta-style <memory_blocks> XML rendering."""
        view = self.store.build_memory_view(conversation_id, "agent", query)
        core = CoreMemory.from_memory_view(view)
        rendered = core.render()
        return rendered if rendered.strip() else None

    # ------------------------------------------------------------------
    # Write: record turns and cognitive events
    # ------------------------------------------------------------------

    def record_turn(self, conversation_id: str, role: str, content: str, **meta: Any) -> None:
        self.store.append_brain_record(conversation_id, {"role": role, "content": content, **meta})

    def record_tool(self, conversation_id: str, tool_name: str, content: str, **meta: Any) -> None:
        self.store.append_tool_record(conversation_id, {"tool_name": tool_name, "content": content, **meta})

    def record_cognitive_event(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        turn_id: str,
        user_text: str,
        reply: str,
        had_tools: bool = False,
    ) -> None:
        """Write a cognitive event at the end of a turn."""
        event = CognitiveEvent(
            event_id=make_id("cog"),
            agent_id=agent_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            summary=user_text[:120],
            outcome="tool_loop" if had_tools else "direct_reply",
            reason=reply[:200],
            user_text=user_text,
            assistant_text=reply,
            source_event_ids=[turn_id],
            metadata={"had_tools": had_tools},
        )
        self.store.append_patch(MemoryPatch(cognitive_append=[event]))

    async def consolidate_turn(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        turn_id: str,
        user_text: str,
        reply: str,
        user_id: str = "",
    ) -> Any:
        if self.consolidation_agent is None:
            return None
        return await self.consolidation_agent.run_for_turn(
            agent_id=agent_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            latest_user_text=user_text,
            latest_reply=reply,
            user_id=user_id,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

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
