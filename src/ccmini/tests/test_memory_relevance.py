from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ccmini.memory.adapter import MemoryAdapter
from ccmini.memory.store import JsonlMemoryStore
from ccmini.memory.types import LongTermRecord, MemoryCandidate, MemoryPatch


def _seed_live_check_memory(store: JsonlMemoryStore) -> None:
    store.append_patch(
        MemoryPatch(
            long_term_append=[
                LongTermRecord(
                    agent_id="agent",
                    summary="User requested an exact string response; assistant complied.",
                    memory_candidates=[
                        MemoryCandidate(
                            memory_id="cand_live_query",
                            memory_type="project",
                            summary="Reply with exactly LIVE_QUERY_OK",
                            detail="reply=LIVE_QUERY_OK",
                            confidence=0.45,
                            stability=0.35,
                            tags=["reply", "exactly", "live_query_ok"],
                        )
                    ],
                )
            ]
        )
    )


def test_query_long_term_skips_irrelevant_live_check_memory(tmp_path: Path) -> None:
    store = JsonlMemoryStore(tmp_path / "profile")
    _seed_live_check_memory(store)

    results = store.query_long_term("你好", "agent", limit=5)

    assert results == []


def test_memory_attachment_source_keeps_matching_live_check_memory(tmp_path: Path) -> None:
    store = JsonlMemoryStore(tmp_path / "profile")
    _seed_live_check_memory(store)
    adapter = MemoryAdapter(store)

    results = adapter.find_relevant_memories("conv-1", "请回复 LIVE_QUERY_OK")

    assert len(results) == 1
    assert results[0]["content"] == "Reply with exactly LIVE_QUERY_OK"
