"""Tests for the profile workspace loader."""

from pathlib import Path

import pytest

from reachy_mini.agent_runtime.profile_loader import load_profile_workspace


def _write_profile_fixture(profile_root: Path) -> None:
    for filename, content in {
        "AGENTS.md": "agent rules",
        "USER.md": "user context",
        "SOUL.md": "persona",
        "TOOLS.md": "tool policy",
        "FRONT.md": "front style",
        "config.jsonl": '{"kind":"profile","name":"demo"}\n',
    }.items():
        (profile_root / filename).write_text(content, encoding="utf-8")

    for directory in ("memory", "skills", "session", "tools", "prompts"):
        (profile_root / directory).mkdir()


def test_load_profile_workspace(tmp_path: Path) -> None:
    """Load a valid profile workspace."""
    profile_root = tmp_path / "demo"
    profile_root.mkdir()
    _write_profile_fixture(profile_root)

    workspace = load_profile_workspace(profile_root)

    assert workspace.name == "demo"
    assert workspace.agents_md == "agent rules"
    assert workspace.user_md == "user context"
    assert workspace.soul_md == "persona"
    assert workspace.tools_md == "tool policy"
    assert workspace.front_md == "front style"
    assert workspace.config_records == [{"kind": "profile", "name": "demo"}]
    assert workspace.memory_dir == profile_root / "memory"
    assert workspace.tools_dir == profile_root / "tools"


def test_load_profile_workspace_requires_all_files(tmp_path: Path) -> None:
    """Reject workspaces missing required files."""
    profile_root = tmp_path / "missing"
    profile_root.mkdir()
    _write_profile_fixture(profile_root)
    (profile_root / "SOUL.md").unlink()

    with pytest.raises(FileNotFoundError):
        load_profile_workspace(profile_root)


def test_load_profile_workspace_requires_object_jsonl_records(tmp_path: Path) -> None:
    """Reject JSONL lines that are not JSON objects."""
    profile_root = tmp_path / "bad_jsonl"
    profile_root.mkdir()
    _write_profile_fixture(profile_root)
    (profile_root / "config.jsonl").write_text('"oops"\n', encoding="utf-8")

    with pytest.raises(ValueError):
        load_profile_workspace(profile_root)
