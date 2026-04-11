"""JsonlMemoryStore — the full 3-layer memory storage engine.

Ported from reachy_mini.core.memory.JsonlMemoryStore with zero
LangChain dependencies. Data format is 100% compatible with
existing memory/session JSONL files.
"""

from __future__ import annotations

import re
from pathlib import Path
from threading import Lock
from typing import Any

from .types import CognitiveEvent, LongTermRecord, MemoryPatch, MemoryView
from .utils import append_jsonl, ensure_directory, make_id, now_iso, read_jsonl, read_text, write_text


class JsonlMemoryStore:
    """3-layer JSONL memory store.

    Layer 1 (Raw):      session/<conv_id>/{brain,tool,event}.jsonl
    Layer 2 (Cognitive): memory/cognitive_events.jsonl
    Layer 3 (Long-term): memory/memory.jsonl + projection files
    """

    def __init__(
        self,
        profile_root: Path,
        *,
        memory_root: Path | None = None,
        session_root: Path | None = None,
    ) -> None:
        self.profile_root = Path(profile_root)
        self.memory_root = ensure_directory(
            Path(memory_root) if memory_root is not None else self.profile_root / "memory"
        )
        self.session_root = ensure_directory(
            Path(session_root) if session_root is not None else self.profile_root / "session"
        )
        self.write_lock = Lock()

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    @property
    def long_term_path(self) -> Path:
        return self.memory_root / "memory.jsonl"

    @property
    def cognitive_path(self) -> Path:
        return self.memory_root / "cognitive_events.jsonl"

    # ------------------------------------------------------------------
    # Layer 1: Raw — per-conversation streams
    # ------------------------------------------------------------------

    def append_brain_record(self, conversation_id: str, payload: dict[str, Any]) -> None:
        row = dict(payload)
        row.setdefault("created_at", now_iso())
        with self.write_lock:
            append_jsonl(self.path_for_conversation_stream(conversation_id, "brain.jsonl"), [row])

    def append_tool_record(self, conversation_id: str, payload: dict[str, Any]) -> None:
        row = dict(payload)
        row.setdefault("created_at", now_iso())
        with self.write_lock:
            append_jsonl(self.path_for_conversation_stream(conversation_id, "tool.jsonl"), [row])

    def append_event_record(self, conversation_id: str, payload: dict[str, Any]) -> None:
        """General event stream (replaces the old 'front' stream)."""
        row = dict(payload)
        row.setdefault("created_at", now_iso())
        with self.write_lock:
            append_jsonl(self.path_for_conversation_stream(conversation_id, "event.jsonl"), [row])

    # ------------------------------------------------------------------
    # Layer 2 + 3: Patches (cognitive + long-term)
    # ------------------------------------------------------------------

    def append_patch(self, patch: MemoryPatch) -> None:
        cognitive_rows = [self._normalize_cognitive(item.model_dump()) for item in patch.cognitive_append]
        long_term_rows = [self._normalize_long_term(item.model_dump()) for item in patch.long_term_append]
        if patch.user_updates:
            long_term_rows.append(
                self._normalize_long_term(
                    LongTermRecord(
                        record_id=make_id("mem"),
                        summary="projection updates",
                        user_updates=list(dict.fromkeys(patch.user_updates)),
                    ).model_dump()
                )
            )

        with self.write_lock:
            if cognitive_rows:
                append_jsonl(self.cognitive_path, cognitive_rows)
            if long_term_rows:
                append_jsonl(self.long_term_path, long_term_rows)

        if long_term_rows:
            self.refresh_projections()

    # ------------------------------------------------------------------
    # Build MemoryView (read all 3 layers)
    # ------------------------------------------------------------------

    def build_memory_view(
        self,
        conversation_id: str,
        agent_id: str,
        query: str,
        limit: int = 6,
    ) -> MemoryView:
        return MemoryView(
            raw_layer={
                "recent_dialogue": self.recent_brain_records(conversation_id, limit),
                "recent_front_events": self.recent_event_records(conversation_id, limit),
                "recent_tools": self.recent_tool_records(conversation_id, limit),
            },
            cognitive_layer=self.recent_cognitive_events(conversation_id, limit),
            long_term_layer={
                "summary": self.build_long_term_summary(query=query, agent_id=agent_id, limit=limit),
                "records": self.query_long_term(query=query, agent_id=agent_id, limit=limit),
            },
            projections={
                "user_anchor": read_text(self.profile_root / "USER.md"),
            },
        )

    # ------------------------------------------------------------------
    # Layer 1 reads
    # ------------------------------------------------------------------

    def recent_brain_records(self, conversation_id: str, limit: int) -> list[dict[str, Any]]:
        rows = read_jsonl(self.path_for_conversation_stream(conversation_id, "brain.jsonl"))
        return rows[-limit:] if limit > 0 else rows

    def recent_event_records(self, conversation_id: str, limit: int) -> list[dict[str, Any]]:
        path = self.path_for_conversation_stream(conversation_id, "event.jsonl")
        if not path.exists():
            path = self.path_for_conversation_stream(conversation_id, "front.jsonl")
        rows = read_jsonl(path)
        return rows[-limit:] if limit > 0 else rows

    def recent_tool_records(self, conversation_id: str, limit: int) -> list[dict[str, Any]]:
        rows = read_jsonl(self.path_for_conversation_stream(conversation_id, "tool.jsonl"))
        return rows[-limit:] if limit > 0 else rows

    # ------------------------------------------------------------------
    # Layer 2 reads
    # ------------------------------------------------------------------

    def recent_cognitive_events(self, conversation_id: str, limit: int) -> list[dict[str, Any]]:
        rows = [
            row for row in read_jsonl(self.cognitive_path)
            if self._matches_conversation(row, conversation_id)
        ]
        return rows[-limit:] if limit > 0 else rows

    # ------------------------------------------------------------------
    # Layer 3 reads
    # ------------------------------------------------------------------

    def query_long_term(self, query: str, agent_id: str, limit: int) -> list[dict[str, Any]]:
        rows: list[tuple[float, dict[str, Any]]] = []
        tokens = self._tokenize(query)
        if not tokens:
            return []
        for row in read_jsonl(self.long_term_path):
            if not self._matches_agent(row, agent_id):
                continue
            for candidate in row.get("memory_candidates", []) or []:
                if not isinstance(candidate, dict):
                    continue
                payload = dict(candidate)
                payload["record_summary"] = str(row.get("summary", "") or "")
                score = self._score_candidate(payload, tokens)
                if score <= 0:
                    continue
                rows.append((score, payload))
        rows.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in rows[:limit]]

    def build_long_term_summary(self, query: str, agent_id: str, limit: int) -> str:
        rows = self.query_long_term(query=query, agent_id=agent_id, limit=limit)
        lines: list[str] = []
        for row in rows:
            summary = str(row.get("summary", "") or "").strip()
            memory_type = str(row.get("memory_type", "") or "").strip()
            if summary:
                lines.append(f"- [{memory_type}] {summary}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Projection files (USER.md auto-evolution)
    # ------------------------------------------------------------------

    def refresh_projections(self) -> None:
        user_updates: list[str] = []
        for row in read_jsonl(self.long_term_path):
            for item in row.get("user_updates", []) or []:
                text = str(item or "").strip()
                if text and text not in user_updates:
                    user_updates.append(text)

        if user_updates:
            self._merge_projection_file(self.profile_root / "USER.md", "用户画像", user_updates)

    def _merge_projection_file(self, path: Path, title: str, updates: list[str]) -> None:
        normalized = [s.strip() for s in updates if s.strip()]
        if not normalized:
            return
        existing = read_text(path)
        if not existing.strip():
            lines = [f"# {title}", ""] + [f"- {item}" for item in dict.fromkeys(normalized)]
            write_text(path, "\n".join(lines) + "\n")
            return
        existing_bullets = self._extract_bullet_rows(existing)
        to_append = [item for item in normalized if item not in existing_bullets]
        if not to_append:
            return
        filtered = [
            line for line in existing.splitlines()
            if line.strip() != "- 暂无稳定沉淀"
        ]
        merged = "\n".join(filtered).rstrip()
        if merged:
            merged += "\n"
        merged += "".join(f"- {item}\n" for item in to_append)
        write_text(path, merged)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def path_for_conversation_stream(self, conversation_id: str, filename: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", conversation_id)
        return ensure_directory(self.session_root / safe) / filename

    @staticmethod
    def _matches_conversation(row: dict[str, Any], conversation_id: str) -> bool:
        if not conversation_id:
            return True
        row_conv = str(row.get("conversation_id", "") or "").strip()
        return not row_conv or row_conv == conversation_id

    @staticmethod
    def _matches_agent(row: dict[str, Any], agent_id: str) -> bool:
        if not agent_id:
            return True
        row_agent = str(row.get("agent_id", "") or "").strip()
        return not row_agent or row_agent == agent_id

    def _normalize_cognitive(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = CognitiveEvent.model_validate(payload)
        if not row.event_id:
            row.event_id = make_id("cog")
        return row.model_dump()

    def _normalize_long_term(self, payload: dict[str, Any]) -> dict[str, Any]:
        row = LongTermRecord.model_validate(payload)
        if not row.record_id:
            row.record_id = make_id("mem")
        return row.model_dump()

    @staticmethod
    def _extract_bullet_rows(content: str) -> set[str]:
        rows: set[str] = set()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                value = stripped[2:].strip()
                if value:
                    rows.add(value)
        return rows

    def _score_candidate(self, candidate: dict[str, Any], tokens: set[str]) -> float:
        if not tokens:
            return 0.0
        haystack = " ".join([
            str(candidate.get("summary", "") or ""),
            str(candidate.get("detail", "") or ""),
            " ".join(str(t).strip() for t in candidate.get("tags", []) or [] if str(t).strip()),
        ])
        candidate_tokens = self._tokenize(haystack)
        overlap_tokens = tokens & candidate_tokens
        if not overlap_tokens:
            return 0.0
        overlap = len(overlap_tokens) / max(1, len(tokens))
        confidence = float(candidate.get("confidence", 0.0) or 0.0)
        stability = float(candidate.get("stability", 0.0) or 0.0)
        return (overlap * 0.6) + (confidence * 0.25) + (stability * 0.15)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {t for t in re.split(r"[^\w\u4e00-\u9fff]+", str(text or "").lower()) if t}
