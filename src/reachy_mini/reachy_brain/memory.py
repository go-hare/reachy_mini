"""Small JSONL memory store for v4 worker task records."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class WorkerMemory:
    """Append/read JSONL records for one worker namespace."""

    root: Path
    namespace: str

    @property
    def path(self) -> Path:
        """Return the namespace JSONL file path."""
        return self.root / f"{self.namespace}.jsonl"

    def append(self, record: dict[str, Any]) -> str:
        """Append a JSON-serializable worker record and return its row id."""
        self.root.mkdir(parents=True, exist_ok=True)
        row_id = str(record.get("id") or uuid.uuid4().hex)
        payload = {"id": row_id, **record}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return row_id

    def read_all(self) -> list[dict[str, Any]]:
        """Read all records in this namespace."""
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for raw_line in self.path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    def read(
        self,
        filter: dict[str, Any] | Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Read records matching an optional dict or predicate filter."""
        rows = self.read_all()
        if filter is None:
            return rows
        if callable(filter):
            return [row for row in rows if filter(row)]
        return [
            row
            for row in rows
            if all(row.get(key) == value for key, value in filter.items())
        ]
