"""ConsolidationAgent: background memory consolidation.

Extracts stable memories from each turn and writes them to the
long-term memory layer.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from ..providers import BaseProvider
from ..messages import user_message
from .store import JsonlMemoryStore
from .types import LongTermRecord, MemoryCandidate, MemoryPatch
from .utils import make_id


class ConsolidationDigest(BaseModel):
    agent_id: str
    conversation_id: str
    user_id: str = ""
    turn_id: str
    latest_user_text: str
    latest_reply: str
    long_term_summary: str = ""
    user_anchor: str = ""


class ConsolidationOutcome(BaseModel):
    summary: str = ""
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)
    user_updates: list[str] = Field(default_factory=list)
    notes: str = ""


class ConsolidationAgent:
    """Digest one turn, then write stable traces into long-term memory.

    Supports two modes:
    - With a provider: uses LLM reflection for richer extraction
    - Without a provider: falls back to heuristic extraction
    """

    def __init__(
        self,
        *,
        store: JsonlMemoryStore,
        provider: BaseProvider | None = None,
    ) -> None:
        self.store = store
        self.provider = provider

    async def run_for_turn(
        self,
        *,
        agent_id: str,
        conversation_id: str,
        turn_id: str,
        latest_user_text: str,
        latest_reply: str,
        user_id: str = "",
    ) -> ConsolidationOutcome:
        digest = self._build_digest(
            agent_id=agent_id,
            conversation_id=conversation_id,
            user_id=user_id,
            turn_id=turn_id,
            latest_user_text=latest_user_text,
            latest_reply=latest_reply,
        )

        if self.provider is not None:
            try:
                outcome = await self._plan_with_llm(digest)
                outcome = self._merge_with_heuristic(outcome, digest)
            except Exception as exc:
                raise RuntimeError(f"Memory consolidation LLM planning failed: {exc}") from exc
        else:
            outcome = self._build_default_outcome(digest)

        self._apply_outcome(
            agent_id=agent_id,
            conversation_id=conversation_id,
            user_id=user_id,
            turn_id=turn_id,
            outcome=outcome,
        )
        return outcome

    # ------------------------------------------------------------------
    # Build digest
    # ------------------------------------------------------------------

    def _build_digest(
        self,
        *,
        agent_id: str,
        conversation_id: str,
        user_id: str,
        turn_id: str,
        latest_user_text: str,
        latest_reply: str,
    ) -> ConsolidationDigest:
        view = self.store.build_memory_view(conversation_id, agent_id, latest_user_text)
        return ConsolidationDigest(
            agent_id=agent_id,
            conversation_id=conversation_id,
            user_id=user_id,
            turn_id=turn_id,
            latest_user_text=latest_user_text,
            latest_reply=latest_reply,
            long_term_summary=str(view.long_term_layer.get("summary", "") or "").strip(),
            user_anchor=str(view.projections.get("user_anchor", "") or "").strip(),
        )

    # ------------------------------------------------------------------
    # Heuristic consolidation
    # ------------------------------------------------------------------

    def _build_default_outcome(self, digest: ConsolidationDigest) -> ConsolidationOutcome:
        summary = self._clip(f"User asked: {digest.latest_user_text}", 160)

        candidate = MemoryCandidate(
            memory_id=make_id("cand"),
            memory_type="project",
            summary=self._clip(digest.latest_user_text, 120),
            detail=f"reply={self._clip(digest.latest_reply, 160)}",
            confidence=0.45,
            stability=0.35,
            tags=self._extract_tags(f"{digest.latest_user_text} {digest.latest_reply}"),
            metadata={"source": "consolidation_heuristic"},
        )

        user_updates = self._extract_user_updates(digest.latest_user_text)
        return ConsolidationOutcome(
            summary=summary,
            memory_candidates=[candidate],
            user_updates=user_updates,
            notes="heuristic consolidation",
        )

    # ------------------------------------------------------------------
    # LLM-based reflection
    # ------------------------------------------------------------------

    async def _plan_with_llm(self, digest: ConsolidationDigest) -> ConsolidationOutcome:
        assert self.provider is not None

        digest_json = json.dumps(digest.model_dump(), ensure_ascii=False, indent=2)
        user_anchor = self._clip(digest.user_anchor, 2000) if digest.user_anchor else "(empty)"

        prompt = (
            f"Current USER.md anchor:\n{user_anchor}\n\n"
            f"Turn digest JSON:\n{digest_json}\n\n"
            "Return a JSON object with keys: summary, memory_candidates, "
            "user_updates, notes."
        )

        system = (
            "You are the memory consolidation planner for an autonomous coding assistant.\n"
            "Read the turn digest and return a concise structured reflection.\n"
            "Prefer stable, reusable memory only. Avoid fluff. Avoid inventing facts.\n"
            "Keep memory_candidates to at most 3 items.\n"
            "Allowed memory_type values: user, feedback, project, reference.\n"
            "When the user states a stable preference, include it in user_updates.\n"
            "If uncertain, set user_updates to an empty array."
        )

        response = await self.provider.complete(
            messages=[user_message(prompt)],
            system=system,
            max_tokens=1024,
            temperature=0.0,
            query_source="memory_consolidation",
        )

        return self._parse_outcome(response.text, digest)

    def _parse_outcome(self, text: str, digest: ConsolidationDigest) -> ConsolidationOutcome:
        value = text.strip()
        if not value:
            return self._build_default_outcome(digest)
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            start = value.find("{")
            end = value.rfind("}")
            if start < 0 or end <= start:
                return self._build_default_outcome(digest)
            try:
                payload = json.loads(value[start:end + 1])
            except json.JSONDecodeError:
                return self._build_default_outcome(digest)

        if not isinstance(payload, dict):
            return self._build_default_outcome(digest)

        candidates = []
        for item in (payload.get("memory_candidates") or [])[:3]:
            if isinstance(item, dict):
                c = MemoryCandidate.model_validate(item)
                if not c.memory_id:
                    c.memory_id = make_id("cand")
                c.metadata.setdefault("source", "consolidation_llm")
                candidates.append(c)

        return ConsolidationOutcome(
            summary=str(payload.get("summary", "") or "").strip() or self._clip(digest.latest_user_text, 160),
            memory_candidates=candidates,
            user_updates=self._dedupe(payload.get("user_updates", []))[:4],
            notes=str(payload.get("notes", "llm_reflection") or "llm_reflection"),
        )

    def _merge_with_heuristic(
        self,
        outcome: ConsolidationOutcome,
        digest: ConsolidationDigest,
    ) -> ConsolidationOutcome:
        heuristic = self._build_default_outcome(digest)
        merged_candidates = list(outcome.memory_candidates) or list(heuristic.memory_candidates)
        merged_user = self._dedupe(list(outcome.user_updates) + list(heuristic.user_updates))[:4]

        if merged_candidates == list(outcome.memory_candidates) and merged_user == list(outcome.user_updates):
            return outcome

        return ConsolidationOutcome(
            summary=outcome.summary or heuristic.summary,
            memory_candidates=merged_candidates[:3],
            user_updates=merged_user,
            notes=f"{outcome.notes or 'llm'}; heuristic_backfill",
        )

    # ------------------------------------------------------------------
    # Apply outcome to store
    # ------------------------------------------------------------------

    def _apply_outcome(
        self,
        *,
        agent_id: str,
        conversation_id: str,
        user_id: str,
        turn_id: str,
        outcome: ConsolidationOutcome,
    ) -> None:
        has_memory = bool(outcome.memory_candidates or outcome.user_updates)
        if not has_memory:
            return
        record = LongTermRecord(
            record_id=make_id("mem"),
            user_id=user_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            summary=outcome.summary or "consolidation",
            memory_candidates=list(outcome.memory_candidates),
            user_updates=list(outcome.user_updates),
            source_event_ids=[turn_id],
        )
        self.store.append_patch(MemoryPatch(long_term_append=[record]))

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_user_updates(user_text: str) -> list[str]:
        text = str(user_text or "").strip()
        if not text:
            return []
        rules = [
            r"(我喜欢[^。！？!\n]+)", r"(我不喜欢[^。！？!\n]+)",
            r"(我是[^。！？!\n]+)", r"(我想要[^。！？!\n]+)",
            r"(I like[^.!?\n]+)", r"(I don't like[^.!?\n]+)",
            r"(I am[^.!?\n]+)", r"(I want[^.!?\n]+)",
        ]
        updates: list[str] = []
        for pattern in rules:
            for match in re.findall(pattern, text, flags=re.IGNORECASE):
                cleaned = str(match).strip()
                if cleaned and cleaned not in updates:
                    updates.append(cleaned)
        return updates[:4]

    @staticmethod
    def _extract_tags(text: str) -> list[str]:
        tokens = [t for t in re.split(r"[^\w\u4e00-\u9fff]+", str(text or "").lower()) if t and len(t) >= 2]
        tags: list[str] = []
        for token in tokens:
            if token not in tags:
                tags.append(token)
            if len(tags) >= 8:
                break
        return tags

    @staticmethod
    def _dedupe(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        rows: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in rows:
                rows.append(text)
        return rows

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        value = str(text or "").strip()
        return value if len(value) <= limit else value[:limit].rstrip() + "..."
