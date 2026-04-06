"""SendUserFile — deliver a project file to the user-facing inbox (Kairos)."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from ..kairos.inbox import record_file_delivery
from ..tool import Tool, ToolUseContext

logger = logging.getLogger(__name__)

_MAX_BYTES = 5_000_000


def _safe_under_cwd(path: Path, cwd: Path) -> bool:
    try:
        resolved = path.resolve()
        return resolved.is_relative_to(cwd.resolve())
    except (OSError, ValueError):
        return False


class SendUserFileTool(Tool):
    """Expose a file path to the host inbox (brief / companion UI), with metadata."""

    name = "SendUserFile"
    description = (
        "Deliver an existing file to the user's inbox (Kairos). Use for logs, "
        "diffs, or artifacts the user should open outside the chat. "
        "Path must be under the current working directory."
    )
    is_read_only = True

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to a file under the project cwd.",
                },
                "caption": {
                    "type": "string",
                    "description": "Short description of why this file is being sent.",
                },
            },
            "required": ["file_path"],
        }

    async def execute(
        self,
        *,
        context: ToolUseContext,
        file_path: str = "",
        caption: str = "",
        **kwargs: Any,
    ) -> str:
        import os

        raw = (file_path or "").strip()
        if not raw:
            return json.dumps({"ok": False, "error": "file_path is required"}, ensure_ascii=False)

        cwd = Path(os.getcwd())
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate

        if not _safe_under_cwd(candidate, cwd):
            return json.dumps(
                {
                    "ok": False,
                    "error": "file_path must resolve under the current working directory",
                },
                ensure_ascii=False,
            )

        if not candidate.is_file():
            return json.dumps(
                {"ok": False, "error": f"not a file: {candidate}"},
                ensure_ascii=False,
            )

        size = candidate.stat().st_size
        if size > _MAX_BYTES:
            return json.dumps(
                {
                    "ok": False,
                    "error": f"file too large ({size} bytes; max {_MAX_BYTES})",
                },
                ensure_ascii=False,
            )

        digest = hashlib.sha256()
        with candidate.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        sha = digest.hexdigest()

        conv = context.conversation_id or "default"
        record_file_delivery(
            conversation_id=conv,
            source_path=str(candidate.resolve()),
            byte_length=size,
            content_sha256=sha,
            caption=caption or "",
        )
        logger.info("SendUserFile recorded: %s (%d bytes)", candidate, size)
        return json.dumps(
            {
                "ok": True,
                "path": str(candidate.resolve()),
                "byte_length": size,
                "content_sha256": sha,
                "caption": caption or "",
            },
            ensure_ascii=False,
        )
