"""Profile workspace loading utilities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED_DOCUMENTS = (
    "AGENTS.md",
    "USER.md",
    "SOUL.md",
    "TOOLS.md",
    "FRONT.md",
)
REQUIRED_DIRECTORIES = (
    "memory",
    "skills",
    "session",
    "tools",
    "prompts",
)
CONFIG_FILE = "config.jsonl"


@dataclass(frozen=True)
class ProfileWorkspace:
    """Structured view of a profile workspace."""

    name: str
    root: Path
    agents_md: str
    user_md: str
    soul_md: str
    tools_md: str
    front_md: str
    config_records: list[dict[str, Any]]
    memory_dir: Path
    skills_dir: Path
    session_dir: Path
    tools_dir: Path
    prompts_dir: Path


def _read_text_file(path: Path) -> str:
    """Read a UTF-8 text file."""
    return path.read_text(encoding="utf-8")


def _load_config_records(path: Path) -> list[dict[str, Any]]:
    """Load JSONL config records."""
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in {path} at line {line_number}: {exc.msg}"
            ) from exc

        if not isinstance(record, dict):
            raise ValueError(
                f"Expected a JSON object in {path} at line {line_number}, "
                f"got {type(record).__name__}."
            )

        records.append(record)

    return records


def _require_path(path: Path, *, directory: bool = False) -> Path:
    """Ensure a required file or directory exists."""
    if directory:
        if not path.is_dir():
            raise FileNotFoundError(f"Required directory is missing: {path}")
    else:
        if not path.is_file():
            raise FileNotFoundError(f"Required file is missing: {path}")
    return path


def load_profile_workspace(profile_root: Path) -> ProfileWorkspace:
    """Load and validate a profile workspace from disk."""
    root = profile_root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Profile workspace does not exist: {root}")

    document_paths = {
        filename: _require_path(root / filename) for filename in REQUIRED_DOCUMENTS
    }
    config_path = _require_path(root / CONFIG_FILE)
    directory_paths = {
        directory: _require_path(root / directory, directory=True)
        for directory in REQUIRED_DIRECTORIES
    }

    return ProfileWorkspace(
        name=root.name,
        root=root,
        agents_md=_read_text_file(document_paths["AGENTS.md"]),
        user_md=_read_text_file(document_paths["USER.md"]),
        soul_md=_read_text_file(document_paths["SOUL.md"]),
        tools_md=_read_text_file(document_paths["TOOLS.md"]),
        front_md=_read_text_file(document_paths["FRONT.md"]),
        config_records=_load_config_records(config_path),
        memory_dir=directory_paths["memory"],
        skills_dir=directory_paths["skills"],
        session_dir=directory_paths["session"],
        tools_dir=directory_paths["tools"],
        prompts_dir=directory_paths["prompts"],
    )
