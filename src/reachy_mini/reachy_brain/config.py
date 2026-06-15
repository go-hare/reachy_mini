"""Profile-to-AgentConfig adapter for the v4 brain runtime."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)
CONFIG_FILE = "config.jsonl"


class AgentConfigError(ValueError):
    """Base class for v4 AgentConfig loading errors."""


class MissingModelConfigError(AgentConfigError):
    """Raised when no model config exists in the profile."""


class MissingApiKeyError(AgentConfigError):
    """Raised when an env-based API key reference cannot be resolved."""


@dataclass(frozen=True)
class ModelConfig:
    """Brain model configuration."""

    provider: str
    model: str
    base_url: str | None = None
    api_key_ref: str = ""
    api_key: str = ""
    temperature: float = 0.2
    max_tokens: int | None = None
    system_prompt_path: str | None = None
    tool_call_mode: str = "native"


@dataclass(frozen=True)
class SpeechConfig:
    """TTS output configuration."""

    enabled: bool = False
    provider: str = "kokoro"
    model: str = "hexgrad/Kokoro-82M-v1.1-zh"
    voice: str = "zf_001"
    speed: float = 1.0
    sample_rate: int = 24000


@dataclass(frozen=True)
class SpeechInputConfig:
    """Streaming STT input configuration."""

    enabled: bool = False
    provider: str = "funasr"
    base_url: str = "ws://127.0.0.1:10096"
    model: str = "2pass"
    language: str = "zh"
    playback_block_cooldown_ms: int = 700
    stream_chunk_size: tuple[int, int, int] = (5, 10, 5)
    stream_chunk_interval: int = 10
    stream_encoder_chunk_look_back: int = 4
    stream_decoder_chunk_look_back: int = 0
    stream_finish_timeout_s: float = 6.0
    stream_itn: bool = True


@dataclass(frozen=True)
class VisionConfig:
    """Vision input configuration."""

    no_camera: bool = True
    head_tracker: str = "none"
    local_vision: bool = False
    frame_rate: int = 15
    min_confidence: float = 0.5
    emotion_backend: str = "torchscript"
    emotion_model_path: str = ""
    emotion_model_name: str = "enet_b2_7"
    emotion_engine: str = "onnx"
    emotion_device: str = "auto"
    emotion_min_interval_s: float = 0.25
    poster_var_model_path: str = ""
    face_identity_enabled: bool = False
    known_faces_dir: str = ""
    face_identity_threshold: float = 0.42
    face_identity_model_name: str = "buffalo_l"
    face_identity_min_interval_s: float = 0.5


@dataclass(frozen=True)
class AgentConfig:
    """Clean v4 Brain configuration derived from profile JSONL."""

    model: ModelConfig
    speech: SpeechConfig
    speech_input: SpeechInputConfig
    vision: VisionConfig
    extras: dict[str, Any]


def from_profile(
    profile_path: Path,
    *,
    overrides: dict[str, Any] | None = None,
) -> AgentConfig:
    """Load v4 AgentConfig from a profile directory or config.jsonl file."""
    config_path = _resolve_config_path(profile_path)
    records = _read_config_records(config_path)
    config = _config_from_records(records)
    if overrides:
        config = _apply_overrides(config, overrides)
    return _resolve_config_secrets(config)


def _config_from_records(records: list[dict[str, Any]]) -> AgentConfig:
    model_record: dict[str, Any] | None = None
    speech_record: dict[str, Any] | None = None
    speech_input_record: dict[str, Any] | None = None
    vision_record: dict[str, Any] | None = None
    extras: dict[str, Any] = {}

    for record in records:
        kind = str(record.get("kind", "") or "").strip()
        role = str(record.get("role", "") or "").strip()
        is_kernel = kind == "kernel_model" or (kind == "model" and role == "kernel")
        is_front = kind == "front_model" or (kind == "model" and role in {"", "front"})
        if is_kernel:
            model_record = record
        elif is_front and model_record is None:
            model_record = record
        elif kind == "speech":
            speech_record = record
        elif kind == "speech_input":
            speech_input_record = record
        elif kind == "vision":
            vision_record = record
        elif kind not in {"profile", "front", ""}:
            extras.setdefault(kind, []).append(dict(record))

    if model_record is None:
        raise MissingModelConfigError("Missing model config in profile.")

    if speech_record is None:
        LOGGER.warning("config_default_used: speech missing")
    if speech_input_record is None:
        LOGGER.warning("config_default_used: speech_input missing")
    if vision_record is None:
        LOGGER.warning("config_default_used: vision missing")

    return AgentConfig(
        model=_build_model_config(model_record),
        speech=_build_speech_config(speech_record or {}),
        speech_input=_build_speech_input_config(speech_input_record or {}),
        vision=_build_vision_config(vision_record or {}),
        extras=extras,
    )


def _build_model_config(record: dict[str, Any]) -> ModelConfig:
    provider = str(record.get("provider", "mock") or "mock")
    api_key_ref = str(record.get("api_key") or "").strip()
    return ModelConfig(
        provider=provider,
        model=str(record.get("model", "") or ""),
        base_url=str(record.get("base_url") or "") or None,
        api_key_ref=api_key_ref,
        api_key="",
        temperature=float(record.get("temperature", 0.2)),
        max_tokens=(
            int(record["max_tokens"]) if record.get("max_tokens") is not None else None
        ),
        system_prompt_path=(
            str(record.get("system_prompt_path"))
            if record.get("system_prompt_path") is not None
            else None
        ),
        tool_call_mode=str(record.get("tool_call_mode", "native") or "native"),
    )


def _resolve_config_secrets(config: AgentConfig) -> AgentConfig:
    api_key_ref, api_key = _resolve_api_key(
        config.model.api_key_ref or config.model.api_key,
        provider=config.model.provider,
    )
    return replace(
        config,
        model=replace(config.model, api_key_ref=api_key_ref, api_key=api_key),
    )


def _build_speech_config(record: dict[str, Any]) -> SpeechConfig:
    return SpeechConfig(
        enabled=bool(record.get("enabled", False)),
        provider=str(record.get("provider", "kokoro") or "kokoro"),
        model=str(record.get("model", "hexgrad/Kokoro-82M-v1.1-zh")),
        voice=str(record.get("voice", "zf_001") or "zf_001"),
        speed=float(record.get("speed", 1.0)),
        sample_rate=int(record.get("sample_rate", 24000)),
    )


def _build_speech_input_config(record: dict[str, Any]) -> SpeechInputConfig:
    return SpeechInputConfig(
        enabled=bool(record.get("enabled", False)),
        provider=str(record.get("provider", "funasr") or "funasr"),
        base_url=str(record.get("base_url", "ws://127.0.0.1:10096")),
        model=str(record.get("model", "2pass") or "2pass"),
        language=str(record.get("language", "zh") or "zh"),
        playback_block_cooldown_ms=max(
            0,
            int(record.get("playback_block_cooldown_ms", 700)),
        ),
        stream_chunk_size=_parse_chunk_size(record.get("stream_chunk_size", (5, 10, 5))),
        stream_chunk_interval=max(1, int(record.get("stream_chunk_interval", 10))),
        stream_encoder_chunk_look_back=max(
            0,
            int(record.get("stream_encoder_chunk_look_back", 4)),
        ),
        stream_decoder_chunk_look_back=max(
            0,
            int(record.get("stream_decoder_chunk_look_back", 0)),
        ),
        stream_finish_timeout_s=max(
            0.5,
            float(record.get("stream_finish_timeout_s", 6.0)),
        ),
        stream_itn=bool(record.get("stream_itn", True)),
    )


def _build_vision_config(record: dict[str, Any]) -> VisionConfig:
    return VisionConfig(
        no_camera=bool(record.get("no_camera", True)),
        head_tracker=str(record.get("head_tracker", "none") or "none"),
        local_vision=bool(record.get("local_vision", False)),
        frame_rate=max(1, int(record.get("frame_rate", 15))),
        min_confidence=float(record.get("min_confidence", 0.5)),
        emotion_backend=str(record.get("emotion_backend", "torchscript") or "torchscript"),
        emotion_model_path=str(record.get("emotion_model_path", "") or ""),
        emotion_model_name=str(record.get("emotion_model_name", "enet_b2_7") or "enet_b2_7"),
        emotion_engine=str(record.get("emotion_engine", "onnx") or "onnx"),
        emotion_device=str(record.get("emotion_device", "auto") or "auto"),
        emotion_min_interval_s=max(
            0.0,
            float(record.get("emotion_min_interval_s", 0.25)),
        ),
        poster_var_model_path=str(record.get("poster_var_model_path", "") or ""),
        face_identity_enabled=bool(record.get("face_identity_enabled", False)),
        known_faces_dir=str(record.get("known_faces_dir", "") or ""),
        face_identity_threshold=max(
            0.0,
            float(record.get("face_identity_threshold", 0.42)),
        ),
        face_identity_model_name=str(
            record.get("face_identity_model_name", "buffalo_l") or "buffalo_l"
        ),
        face_identity_min_interval_s=max(
            0.0,
            float(record.get("face_identity_min_interval_s", 0.5)),
        ),
    )


def _resolve_api_key(raw_value: object, *, provider: str) -> tuple[str, str]:
    raw = str(raw_value or "").strip()
    if not raw:
        return "", ""
    if raw.startswith("env:"):
        env_name = raw.removeprefix("env:").strip()
        value = os.environ.get(env_name)
        if not value:
            raise MissingApiKeyError(f"Missing environment variable: {env_name}")
        return raw, value
    if raw.startswith("vault:"):
        return raw, ""
    return raw, raw


def _parse_chunk_size(value: object) -> tuple[int, int, int]:
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        parts = [str(part).strip() for part in value]
    else:
        return (5, 10, 5)
    if len(parts) != 3:
        return (5, 10, 5)
    try:
        return (max(1, int(parts[0])), max(1, int(parts[1])), max(1, int(parts[2])))
    except ValueError:
        return (5, 10, 5)


def _resolve_config_path(profile_path: Path) -> Path:
    path = profile_path.expanduser().resolve()
    if path.is_file():
        return path
    direct = path / CONFIG_FILE
    nested = path / "profiles" / CONFIG_FILE
    if direct.is_file():
        return direct
    if nested.is_file():
        return nested
    raise FileNotFoundError(f"Profile config.jsonl not found under {path}")


def _read_config_records(config_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        config_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in {config_path} at line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(record, dict):
            raise ValueError(
                f"Expected JSON object in {config_path} at line {line_number}."
            )
        records.append(record)
    return records


def _apply_overrides(config: AgentConfig, overrides: dict[str, Any]) -> AgentConfig:
    current = config
    for key, value in overrides.items():
        current = _apply_override(current, key, value)
    return current


def _apply_override(config: AgentConfig, key: str, value: Any) -> AgentConfig:
    section, _, field_name = key.partition(".")
    if not field_name:
        raise ValueError(f"Override must use dot syntax: {key}")
    if section == "model":
        return replace(config, model=replace(config.model, **{field_name: value}))
    if section == "speech":
        return replace(config, speech=replace(config.speech, **{field_name: value}))
    if section == "speech_input":
        return replace(
            config,
            speech_input=replace(config.speech_input, **{field_name: value}),
        )
    if section == "vision":
        return replace(config, vision=replace(config.vision, **{field_name: value}))
    raise ValueError(f"Unknown override section: {section}")
