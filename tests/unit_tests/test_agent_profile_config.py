"""Tests for profile runtime config parsing."""

from pathlib import Path

from reachy_mini.agent_runtime.config import load_agent_profile_config
from reachy_mini.agent_runtime.profile_loader import load_profile_workspace


def _write_profile(profile_root: Path, *, config_jsonl: str) -> None:
    for filename, content in {
        "AGENTS.md": "agent rules",
        "USER.md": "user context",
        "SOUL.md": "soul anchor",
        "TOOLS.md": "tool policy",
        "FRONT.md": "front style",
        "config.jsonl": config_jsonl,
    }.items():
        (profile_root / filename).write_text(content, encoding="utf-8")
    for directory in ("memory", "skills", "session", "tools", "prompts"):
        (profile_root / directory).mkdir()


def test_load_agent_profile_config_reads_front_settings(tmp_path: Path) -> None:
    """Parse front settings from config.jsonl."""
    profile_root = tmp_path / "demo"
    profile_root.mkdir()
    _write_profile(
        profile_root,
        config_jsonl=(
            '{"kind":"front","mode":"text","style":"warm_precise","history_limit":8}\n'
            '{"kind":"front_model","provider":"ollama","model":"qwen2.5:7b","base_url":"http://127.0.0.1:11434","temperature":0.2}\n'
        ),
    )

    profile = load_profile_workspace(profile_root)
    config = load_agent_profile_config(profile)

    assert config.front_mode == "text"
    assert config.front_style == "warm_precise"
    assert config.history_limit == 8
    assert config.front_model.provider == "ollama"
    assert config.front_model.model == "qwen2.5:7b"
    assert config.front_model.base_url == "http://127.0.0.1:11434"
    assert config.front_model.temperature == 0.2


def test_load_agent_profile_config_reads_kernel_settings(tmp_path: Path) -> None:
    """Parse kernel settings from config.jsonl."""
    profile_root = tmp_path / "demo"
    profile_root.mkdir()
    _write_profile(
        profile_root,
        config_jsonl=(
            '{"kind":"model","role":"front","provider":"mock","model":"front-demo","temperature":0.3}\n'
            '{"kind":"model","role":"kernel","provider":"openai","model":"gpt-4.1-mini","base_url":"https://example.com/v1","api_key_env":"KERNEL_API_KEY","temperature":0.1}\n'
        ),
    )

    profile = load_profile_workspace(profile_root)
    config = load_agent_profile_config(profile)

    assert config.front_model.model == "front-demo"
    assert config.kernel_model.provider == "openai"
    assert config.kernel_model.model == "gpt-4.1-mini"
    assert config.kernel_model.base_url == "https://example.com/v1"
    assert config.kernel_model.api_key_env == "KERNEL_API_KEY"
    assert config.kernel_model.temperature == 0.1
