"""Tests for app project scaffolding and profile loading."""

from types import SimpleNamespace
from pathlib import Path

import pytest

from reachy_mini.runtime.main import handle_create
from reachy_mini.runtime.profile_loader import load_profile_bundle
from reachy_mini.runtime.project import create_app_project


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


def test_load_profile_bundle(tmp_path: Path) -> None:
    """Load a valid profile bundle."""
    profile_root = tmp_path / "demo"
    profile_root.mkdir()
    _write_profile_fixture(profile_root)

    bundle = load_profile_bundle(profile_root)

    assert bundle.name == "demo"
    assert bundle.agents_md == "agent rules"
    assert bundle.user_md == "user context"
    assert bundle.soul_md == "persona"
    assert bundle.tools_md == "tool policy"
    assert bundle.front_md == "front style"
    assert bundle.config_records == [{"kind": "profile", "name": "demo"}]
    assert bundle.memory_dir == profile_root / "memory"
    assert bundle.tools_dir == profile_root / "tools"


def test_load_profile_bundle_requires_all_files(tmp_path: Path) -> None:
    """Reject profile bundles missing required files."""
    profile_root = tmp_path / "missing"
    profile_root.mkdir()
    _write_profile_fixture(profile_root)
    (profile_root / "SOUL.md").unlink()

    with pytest.raises(FileNotFoundError):
        load_profile_bundle(profile_root)


def test_load_profile_bundle_requires_object_jsonl_records(tmp_path: Path) -> None:
    """Reject JSONL lines that are not JSON objects."""
    profile_root = tmp_path / "bad_jsonl"
    profile_root.mkdir()
    _write_profile_fixture(profile_root)
    (profile_root / "config.jsonl").write_text('"oops"\n', encoding="utf-8")

    with pytest.raises(ValueError):
        load_profile_bundle(profile_root)


def test_create_app_project_scaffolds_expected_tree(tmp_path: Path) -> None:
    """Create the installable app tree with app and profile directories."""
    project_root = tmp_path / "demo"

    created = create_app_project(project_root, "demo")
    package_root = project_root / "demo"
    bundle_root = project_root / "profiles"

    assert created == project_root.resolve()
    for filename in (
        "README.md",
        "pyproject.toml",
        ".gitignore",
        "index.html",
        "style.css",
    ):
        assert (project_root / filename).is_file()
    for filename in (
        "AGENTS.md",
        "USER.md",
        "SOUL.md",
        "TOOLS.md",
        "FRONT.md",
        "config.jsonl",
        "__init__.py",
    ):
        assert (bundle_root / filename).is_file()
    for directory in ("memory", "skills", "session", "tools", "prompts"):
        assert (bundle_root / directory).is_dir()
    assert (package_root / "static" / "index.html").is_file()
    assert (package_root / "static" / "style.css").is_file()
    assert (package_root / "static" / "main.js").is_file()
    assert (package_root / "__init__.py").is_file()
    assert (package_root / "main.py").is_file()

    bundle = load_profile_bundle(project_root)
    assert bundle.root == bundle_root.resolve()
    assert bundle.name == "demo"


def test_handle_create_normalizes_app_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI creation should normalize the app name before creating files."""
    args = SimpleNamespace(
        app_name="demo-app",
        apps_root=tmp_path,
    )

    handle_create(args)

    created_root = tmp_path / "demo_app"
    assert created_root.is_dir()
    assert (created_root / "demo_app" / "main.py").is_file()
    assert (created_root / "profiles" / "AGENTS.md").is_file()
    main_py = (created_root / "demo_app" / "main.py").read_text(encoding="utf-8")
    assert 'profile_root_relative_path = "profiles"' in main_py
    pyproject = (created_root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'demo_app = "demo_app.main:DemoApp"' in pyproject
    assert "Created app:" in capsys.readouterr().out
