"""Runtime configuration loaded from a profile bundle."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from reachy_mini.runtime.profile_loader import ProfileBundle


@dataclass(slots=True)
class FrontModelConfig:
    """How the front layer should talk to a model."""

    provider: str = "mock"
    model: str = "reachy_mini_front_mock"
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.4


@dataclass(slots=True)
class KernelModelConfig:
    """How the kernel layer should talk to a model."""

    provider: str = "mock"
    model: str = "reachy_mini_kernel_mock"
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.2


@dataclass(slots=True)
class VisionRuntimeConfig:
    """How camera, head tracking, and local vision should start."""

    no_camera: bool = False
    head_tracker: str = ""
    local_vision: bool = False
    local_vision_model: str = ""
    hf_home: str = ""


@dataclass(slots=True)
class SpeechRuntimeConfig:
    """How optional reply-audio synthesis and playback should start."""

    enabled: bool = False
    provider: str = ""
    model: str = "gpt-4o-mini-tts"
    base_url: str = ""
    api_key: str = ""
    voice: str = "alloy"
    instructions: str = ""
    speed: float = 1.0
    chunk_ms: int = 80


@dataclass(slots=True)
class SpeechInputRuntimeConfig:
    """How optional robot-microphone capture and transcription should start."""

    enabled: bool = False
    provider: str = ""
    model: str = "gpt-4o-mini-transcribe"
    base_url: str = ""
    api_key: str = ""
    language: str = "zh"
    vad_db_on: float = -36.0
    vad_db_off: float = -46.0
    vad_attack_ms: int = 80
    vad_release_ms: int = 700
    min_utterance_ms: int = 350
    max_utterance_ms: int = 15_000
    playback_block_cooldown_ms: int = 700


@dataclass(slots=True)
class ProfileRuntimeConfig:
    """Structured runtime settings parsed from ``config.jsonl``."""

    front_mode: str = "text"
    front_style: str = "friendly_concise"
    history_limit: int = 6
    front_model: FrontModelConfig = field(default_factory=FrontModelConfig)
    kernel_model: KernelModelConfig = field(default_factory=KernelModelConfig)
    vision: VisionRuntimeConfig = field(default_factory=VisionRuntimeConfig)
    speech: SpeechRuntimeConfig = field(default_factory=SpeechRuntimeConfig)
    speech_input: SpeechInputRuntimeConfig = field(default_factory=SpeechInputRuntimeConfig)


def load_profile_runtime_config(profile: ProfileBundle) -> ProfileRuntimeConfig:
    """Load stage-2 runtime settings from a profile bundle."""
    config = ProfileRuntimeConfig()

    for record in profile.config_records:
        kind = str(record.get("kind", "") or "").strip()
        if kind == "front":
            config.front_mode = str(record.get("mode", config.front_mode) or config.front_mode)
            config.front_style = str(
                record.get("style", config.front_style) or config.front_style
            )
            history_limit = record.get("history_limit")
            if history_limit is not None:
                config.history_limit = max(1, int(history_limit))
            continue

        if kind == "vision":
            config.vision = VisionRuntimeConfig(
                no_camera=bool(record.get("no_camera", config.vision.no_camera)),
                head_tracker=str(
                    record.get("head_tracker", config.vision.head_tracker)
                    or config.vision.head_tracker
                ),
                local_vision=bool(
                    record.get("local_vision", config.vision.local_vision)
                ),
                local_vision_model=str(
                    record.get("local_vision_model", config.vision.local_vision_model)
                    or config.vision.local_vision_model
                ),
                hf_home=str(record.get("hf_home", config.vision.hf_home) or ""),
            )
            continue

        if kind == "speech":
            chunk_ms = record.get("chunk_ms", config.speech.chunk_ms)
            config.speech = SpeechRuntimeConfig(
                enabled=bool(record.get("enabled", config.speech.enabled)),
                provider=str(
                    record.get("provider", config.speech.provider)
                    or config.speech.provider
                ),
                model=str(record.get("model", config.speech.model) or config.speech.model),
                base_url=str(record.get("base_url", config.speech.base_url) or ""),
                api_key=str(record.get("api_key", config.speech.api_key) or ""),
                voice=str(record.get("voice", config.speech.voice) or config.speech.voice),
                instructions=str(
                    record.get("instructions", config.speech.instructions)
                    or config.speech.instructions
                ),
                speed=float(record.get("speed", config.speech.speed)),
                chunk_ms=max(20, int(chunk_ms)) if chunk_ms is not None else config.speech.chunk_ms,
            )
            continue

        if kind == "speech_input":
            config.speech_input = SpeechInputRuntimeConfig(
                enabled=bool(record.get("enabled", config.speech_input.enabled)),
                provider=str(
                    record.get("provider", config.speech_input.provider)
                    or config.speech_input.provider
                ),
                model=str(
                    record.get("model", config.speech_input.model)
                    or config.speech_input.model
                ),
                base_url=str(record.get("base_url", config.speech_input.base_url) or ""),
                api_key=str(record.get("api_key", config.speech_input.api_key) or ""),
                language=str(
                    record.get("language", config.speech_input.language)
                    or config.speech_input.language
                ),
                vad_db_on=float(record.get("vad_db_on", config.speech_input.vad_db_on)),
                vad_db_off=float(
                    record.get("vad_db_off", config.speech_input.vad_db_off)
                ),
                vad_attack_ms=max(
                    20,
                    int(record.get("vad_attack_ms", config.speech_input.vad_attack_ms)),
                ),
                vad_release_ms=max(
                    100,
                    int(record.get("vad_release_ms", config.speech_input.vad_release_ms)),
                ),
                min_utterance_ms=max(
                    100,
                    int(
                        record.get(
                            "min_utterance_ms",
                            config.speech_input.min_utterance_ms,
                        )
                    ),
                ),
                max_utterance_ms=max(
                    1000,
                    int(
                        record.get(
                            "max_utterance_ms",
                            config.speech_input.max_utterance_ms,
                        )
                    ),
                ),
                playback_block_cooldown_ms=max(
                    0,
                    int(
                        record.get(
                            "playback_block_cooldown_ms",
                            config.speech_input.playback_block_cooldown_ms,
                        )
                    ),
                ),
            )
            continue

        if kind not in {"front_model", "kernel_model", "model"}:
            continue

        role = str(record.get("role", "") or "").strip()
        if kind == "front_model" or (kind == "model" and role in {"", "front"}):
            config.front_model = FrontModelConfig(
                provider=str(
                    record.get("provider", config.front_model.provider)
                    or config.front_model.provider
                ),
                model=str(
                    record.get("model", config.front_model.model)
                    or config.front_model.model
                ),
                base_url=str(record.get("base_url", config.front_model.base_url) or ""),
                api_key=str(
                    record.get("api_key", config.front_model.api_key)
                    or config.front_model.api_key
                ),
                temperature=float(record.get("temperature", config.front_model.temperature)),
            )
            continue

        if kind == "kernel_model" or (kind == "model" and role == "kernel"):
            config.kernel_model = KernelModelConfig(
                provider=str(
                    record.get("provider", config.kernel_model.provider)
                    or config.kernel_model.provider
                ),
                model=str(
                    record.get("model", config.kernel_model.model)
                    or config.kernel_model.model
                ),
                base_url=str(record.get("base_url", config.kernel_model.base_url) or ""),
                api_key=str(
                    record.get("api_key", config.kernel_model.api_key)
                    or config.kernel_model.api_key
                ),
                temperature=float(
                    record.get("temperature", config.kernel_model.temperature)
                ),
            )

    return config


def apply_runtime_overrides(
    config: ProfileRuntimeConfig,
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    kernel_provider: str | None = None,
    kernel_model: str | None = None,
    kernel_base_url: str | None = None,
    kernel_api_key: str | None = None,
    kernel_temperature: float | None = None,
    history_limit: int | None = None,
) -> ProfileRuntimeConfig:
    """Apply CLI overrides on top of a parsed profile config."""
    front_model = replace(
        config.front_model,
        provider=provider or config.front_model.provider,
        model=model or config.front_model.model,
        base_url=base_url if base_url is not None else config.front_model.base_url,
        api_key=api_key if api_key is not None else config.front_model.api_key,
        temperature=temperature
        if temperature is not None
        else config.front_model.temperature,
    )
    resolved_kernel_model = replace(
        config.kernel_model,
        provider=kernel_provider or config.kernel_model.provider,
        model=kernel_model or config.kernel_model.model,
        base_url=(
            kernel_base_url if kernel_base_url is not None else config.kernel_model.base_url
        ),
        api_key=(
            kernel_api_key if kernel_api_key is not None else config.kernel_model.api_key
        ),
        temperature=kernel_temperature
        if kernel_temperature is not None
        else config.kernel_model.temperature,
    )
    return ProfileRuntimeConfig(
        front_mode=config.front_mode,
        front_style=config.front_style,
        history_limit=max(1, history_limit)
        if history_limit is not None
        else config.history_limit,
        front_model=front_model,
        kernel_model=resolved_kernel_model,
        vision=config.vision,
        speech=config.speech,
        speech_input=config.speech_input,
    )
