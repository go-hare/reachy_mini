"""Tests for v4 AgentConfig loading from profile JSONL."""

from __future__ import annotations

from pathlib import Path

import pytest

from reachy_mini.reachy_brain.config import (
    MissingApiKeyError,
    from_profile,
)


def _write_config(root: Path, lines: list[str]) -> Path:
    profile_root = root / "profiles"
    profile_root.mkdir(parents=True)
    (profile_root / "config.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return profile_root


def test_from_profile_prefers_kernel_model_and_resolves_env_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kernel_model wins over front_model and env key refs are resolved."""
    monkeypatch.setenv("DEMO_KEY", "resolved-secret")
    profile_root = _write_config(
        tmp_path,
        [
            '{"kind":"front_model","provider":"mock","model":"front"}',
            '{"kind":"kernel_model","provider":"openai","model":"kernel","api_key":"env:DEMO_KEY","temperature":0.1}',
            '{"kind":"speech","enabled":true,"voice":"zf_002","speed":1.2}',
            '{"kind":"speech_input","enabled":true,"stream_chunk_size":[6,12,6]}',
            '{"kind":"vision","no_camera":false,"head_tracker":"yolo","local_vision":true}',
            '{"kind":"experiment","flag":true}',
        ],
    )

    config = from_profile(profile_root)

    assert config.model.model == "kernel"
    assert config.model.api_key_ref == "env:DEMO_KEY"
    assert config.model.api_key == "resolved-secret"
    assert config.model.temperature == 0.1
    assert config.speech.enabled is True
    assert config.speech.voice == "zf_002"
    assert config.speech_input.stream_chunk_size == (6, 12, 6)
    assert config.vision.no_camera is False
    assert config.vision.head_tracker == "yolo"
    assert config.extras["experiment"][0]["flag"] is True


def test_from_profile_falls_back_to_front_model(tmp_path: Path) -> None:
    """front_model is used when kernel_model is missing."""
    profile_root = _write_config(
        tmp_path,
        ['{"kind":"front_model","provider":"mock","model":"front"}'],
    )

    config = from_profile(profile_root)

    assert config.model.model == "front"
    assert config.model.provider == "mock"


def test_from_profile_rejects_missing_env_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing env vars fail before runtime startup."""
    monkeypatch.delenv("MISSING_KEY", raising=False)
    profile_root = _write_config(
        tmp_path,
        ['{"kind":"kernel_model","provider":"openai","model":"demo","api_key":"env:MISSING_KEY"}'],
    )

    with pytest.raises(MissingApiKeyError):
        from_profile(profile_root)


def test_from_profile_accepts_plaintext_key(tmp_path: Path) -> None:
    """Plaintext keys are accepted for self-hosted proxies."""
    profile_root = _write_config(
        tmp_path,
        ['{"kind":"kernel_model","provider":"openai","model":"demo","api_key":"sk-secret"}'],
    )

    config = from_profile(profile_root)
    assert config.model.api_key == "sk-secret"


def test_from_profile_applies_dot_overrides(tmp_path: Path) -> None:
    """CLI/test overrides use dot path syntax after profile merge."""
    profile_root = _write_config(
        tmp_path,
        ['{"kind":"kernel_model","provider":"mock","model":"demo","temperature":0.5}'],
    )

    config = from_profile(
        profile_root / "config.jsonl",
        overrides={"model.temperature": 0.0, "speech.enabled": True},
    )

    assert config.model.temperature == 0.0
    assert config.speech.enabled is True


def test_from_profile_applies_model_key_overrides_before_secret_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mock/text entrypoints can override env-key profiles without requiring secrets."""
    monkeypatch.delenv("DEMO_KEY", raising=False)
    profile_root = _write_config(
        tmp_path,
        ['{"kind":"kernel_model","provider":"openai","model":"demo","api_key":"env:DEMO_KEY"}'],
    )

    config = from_profile(
        profile_root,
        overrides={
            "model.provider": "mock",
            "model.model": "mock",
            "model.api_key_ref": "",
            "model.api_key": "",
        },
    )

    assert config.model.provider == "mock"
    assert config.model.model == "mock"
    assert config.model.api_key_ref == ""
    assert config.model.api_key == ""
