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
    """Parse single-brain settings from config.jsonl."""
    profile_root = tmp_path / "demo"
    profile_root.mkdir()
    _write_profile(
        profile_root,
        config_jsonl=(
            '{"kind":"front","mode":"text","style":"warm_precise","history_limit":8}\n'
            '{"kind":"brain_model","provider":"ollama","model":"qwen2.5:7b","base_url":"http://127.0.0.1:11434","temperature":0.2}\n'
        ),
    )

    profile = load_profile_bundle(profile_root)
    config = load_profile_runtime_config(profile)

    assert config.front_mode == "text"
    assert config.front_style == "warm_precise"
    assert config.history_limit == 8
    assert config.brain_model.provider == "ollama"
    assert config.brain_model.model == "qwen2.5:7b"
    assert config.brain_model.base_url == "http://127.0.0.1:11434"
    assert config.brain_model.temperature == 0.2


def test_load_profile_runtime_config_reads_brain_settings(tmp_path: Path) -> None:
    """Parse brain settings from config.jsonl."""
    profile_root = tmp_path / "demo"
    profile_root.mkdir()
    _write_profile(
        profile_root,
        config_jsonl=(
            '{"kind":"brain_model","provider":"openai","model":"gpt-4.1-mini","base_url":"https://example.com/v1","api_key":"brain-secret","temperature":0.1}\n'
        ),
    )

    profile = load_profile_bundle(profile_root)
    config = load_profile_runtime_config(profile)

    assert config.brain_model.provider == "openai"
    assert config.brain_model.model == "gpt-4.1-mini"
    assert config.brain_model.base_url == "https://example.com/v1"
    assert config.brain_model.api_key == "brain-secret"
    assert config.brain_model.temperature == 0.1


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


def test_load_profile_runtime_config_reads_speech_input_settings(tmp_path: Path) -> None:
    """Parse optional streaming speech-input settings from config.jsonl."""

    profile_root = tmp_path / "demo"
    profile_root.mkdir()
    _write_profile(
        profile_root,
        config_jsonl=(
            '{"kind":"speech_input","enabled":true,"provider":"funasr","base_url":"ws://127.0.0.1:10096","model":"2pass","language":"zh","playback_block_cooldown_ms":950,"stream_chunk_size":[6,12,6],"stream_chunk_interval":12,"stream_encoder_chunk_look_back":5,"stream_decoder_chunk_look_back":1,"stream_finish_timeout_s":7.5,"stream_itn":false}\n'
        ),
    )

    profile = load_profile_bundle(profile_root)
    config = load_profile_runtime_config(profile)

    assert config.speech_input.enabled is True
    assert config.speech_input.provider == "funasr"
    assert config.speech_input.base_url == "ws://127.0.0.1:10096"
    assert config.speech_input.model == "2pass"
    assert config.speech_input.language == "zh"
    assert config.speech_input.playback_block_cooldown_ms == 950
    assert config.speech_input.stream_chunk_size == (6, 12, 6)
    assert config.speech_input.stream_chunk_interval == 12
    assert config.speech_input.stream_encoder_chunk_look_back == 5
    assert config.speech_input.stream_decoder_chunk_look_back == 1
    assert config.speech_input.stream_finish_timeout_s == 7.5
    assert config.speech_input.stream_itn is False
