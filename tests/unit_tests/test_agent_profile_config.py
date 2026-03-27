"""Tests for profile runtime config parsing."""

from pathlib import Path

from reachy_mini.runtime.config import load_profile_runtime_config
from reachy_mini.runtime.profile_loader import load_profile_bundle


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


def test_load_profile_runtime_config_reads_front_settings(tmp_path: Path) -> None:
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

    profile = load_profile_bundle(profile_root)
    config = load_profile_runtime_config(profile)

    assert config.front_mode == "text"
    assert config.front_style == "warm_precise"
    assert config.history_limit == 8
    assert config.front_model.provider == "ollama"
    assert config.front_model.model == "qwen2.5:7b"
    assert config.front_model.base_url == "http://127.0.0.1:11434"
    assert config.front_model.temperature == 0.2


def test_load_profile_runtime_config_reads_kernel_settings(tmp_path: Path) -> None:
    """Parse kernel settings from config.jsonl."""
    profile_root = tmp_path / "demo"
    profile_root.mkdir()
    _write_profile(
        profile_root,
        config_jsonl=(
            '{"kind":"model","role":"front","provider":"mock","model":"front-demo","temperature":0.3}\n'
            '{"kind":"model","role":"kernel","provider":"openai","model":"gpt-4.1-mini","base_url":"https://example.com/v1","api_key":"kernel-secret","temperature":0.1}\n'
        ),
    )

    profile = load_profile_bundle(profile_root)
    config = load_profile_runtime_config(profile)

    assert config.front_model.model == "front-demo"
    assert config.kernel_model.provider == "openai"
    assert config.kernel_model.model == "gpt-4.1-mini"
    assert config.kernel_model.base_url == "https://example.com/v1"
    assert config.kernel_model.api_key == "kernel-secret"
    assert config.kernel_model.temperature == 0.1


def test_load_profile_runtime_config_reads_vision_settings(tmp_path: Path) -> None:
    """Parse legacy-style vision startup settings from config.jsonl."""
    profile_root = tmp_path / "demo"
    profile_root.mkdir()
    _write_profile(
        profile_root,
        config_jsonl=(
            '{"kind":"vision","no_camera":false,"head_tracker":"yolo","local_vision":true,"local_vision_model":"demo-model","hf_home":"./hf-cache"}\n'
        ),
    )

    profile = load_profile_bundle(profile_root)
    config = load_profile_runtime_config(profile)

    assert config.vision.no_camera is False
    assert config.vision.head_tracker == "yolo"
    assert config.vision.local_vision is True
    assert config.vision.local_vision_model == "demo-model"
    assert config.vision.hf_home == "./hf-cache"


def test_load_profile_runtime_config_reads_speech_settings(tmp_path: Path) -> None:
    """Parse optional reply-audio settings from config.jsonl."""
    profile_root = tmp_path / "demo"
    profile_root.mkdir()
    _write_profile(
        profile_root,
        config_jsonl=(
            '{"kind":"speech","enabled":true,"provider":"openai","model":"gpt-4o-mini-tts","voice":"alloy","api_key":"speech-secret","instructions":"Speak warmly.","speed":1.1,"chunk_ms":120}\n'
        ),
    )

    profile = load_profile_bundle(profile_root)
    config = load_profile_runtime_config(profile)

    assert config.speech.enabled is True
    assert config.speech.provider == "openai"
    assert config.speech.model == "gpt-4o-mini-tts"
    assert config.speech.voice == "alloy"
    assert config.speech.api_key == "speech-secret"
    assert config.speech.instructions == "Speak warmly."
    assert config.speech.speed == 1.1
    assert config.speech.chunk_ms == 120
