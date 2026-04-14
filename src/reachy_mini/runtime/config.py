"""Runtime configuration loaded from a profile bundle."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from reachy_mini.runtime.profile_loader import ProfileBundle


def _parse_stream_chunk_size(
    value: object,
    default: tuple[int, int, int],
) -> tuple[int, int, int]:
    """Parse one FunASR chunk-size triple from JSONL config."""

    if isinstance(value, str):
        raw_parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        raw_parts = [str(part).strip() for part in value]
    else:
        return default

    if len(raw_parts) != 3:
        return default

    parsed: list[int] = []
    for part in raw_parts:
        if not part:
            return default
        try:
            parsed.append(max(1, int(part)))
        except (TypeError, ValueError):
            return default
    return (parsed[0], parsed[1], parsed[2])


@dataclass(slots=True)
class BrainModelConfig:
    """How the single brain should talk to a model provider."""

    provider: str = "mock"
    model: str = "reachy_mini_brain_mock"
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
    """How optional robot-microphone capture and streaming transcription should start."""

    enabled: bool = False
    provider: str = ""
    model: str = "2pass"
    base_url: str = ""
    api_key: str = ""
    language: str = "zh"
    playback_block_cooldown_ms: int = 700
    stream_chunk_size: tuple[int, int, int] = (5, 10, 5)
    stream_chunk_interval: int = 10
    stream_encoder_chunk_look_back: int = 4
    stream_decoder_chunk_look_back: int = 0
    stream_finish_timeout_s: float = 6.0
    stream_itn: bool = True


@dataclass(slots=True)
class ProfileRuntimeConfig:
    """Structured runtime settings parsed from ``config.jsonl``."""

    front_mode: str = "text"
    front_style: str = "friendly_concise"
    history_limit: int = 6
    brain_model: BrainModelConfig = field(default_factory=BrainModelConfig)
    vision: VisionRuntimeConfig = field(default_factory=VisionRuntimeConfig)
    speech: SpeechRuntimeConfig = field(default_factory=SpeechRuntimeConfig)
    speech_input: SpeechInputRuntimeConfig = field(default_factory=SpeechInputRuntimeConfig)


def load_profile_runtime_config(profile: ProfileBundle) -> ProfileRuntimeConfig:
    """Load runtime settings from a profile bundle."""

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
                local_vision=bool(record.get("local_vision", config.vision.local_vision)),
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
            stream_chunk_size = _parse_stream_chunk_size(
                record.get("stream_chunk_size", config.speech_input.stream_chunk_size),
                config.speech_input.stream_chunk_size,
            )
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
                playback_block_cooldown_ms=max(
                    0,
                    int(
                        record.get(
                            "playback_block_cooldown_ms",
                            config.speech_input.playback_block_cooldown_ms,
                        )
                    ),
                ),
                stream_chunk_size=stream_chunk_size,
                stream_chunk_interval=max(
                    1,
                    int(
                        record.get(
                            "stream_chunk_interval",
                            config.speech_input.stream_chunk_interval,
                        )
                    ),
                ),
                stream_encoder_chunk_look_back=max(
                    0,
                    int(
                        record.get(
                            "stream_encoder_chunk_look_back",
                            config.speech_input.stream_encoder_chunk_look_back,
                        )
                    ),
                ),
                stream_decoder_chunk_look_back=max(
                    0,
                    int(
                        record.get(
                            "stream_decoder_chunk_look_back",
                            config.speech_input.stream_decoder_chunk_look_back,
                        )
                    ),
                ),
                stream_finish_timeout_s=max(
                    0.5,
                    float(
                        record.get(
                            "stream_finish_timeout_s",
                            config.speech_input.stream_finish_timeout_s,
                        )
                    ),
                ),
                stream_itn=bool(record.get("stream_itn", config.speech_input.stream_itn)),
            )
            continue

        if kind not in {"brain_model", "model"}:
            continue

        config.brain_model = BrainModelConfig(
            provider=str(
                record.get("provider", config.brain_model.provider)
                or config.brain_model.provider
            ),
            model=str(
                record.get("model", config.brain_model.model)
                or config.brain_model.model
            ),
            base_url=str(record.get("base_url", config.brain_model.base_url) or ""),
            api_key=str(record.get("api_key", config.brain_model.api_key) or ""),
            temperature=float(record.get("temperature", config.brain_model.temperature)),
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
    history_limit: int | None = None,
) -> ProfileRuntimeConfig:
    """Apply CLI overrides on top of a parsed profile config."""

    brain_model = replace(
        config.brain_model,
        provider=provider or config.brain_model.provider,
        model=model or config.brain_model.model,
        base_url=base_url if base_url is not None else config.brain_model.base_url,
        api_key=api_key if api_key is not None else config.brain_model.api_key,
        temperature=temperature if temperature is not None else config.brain_model.temperature,
    )
    return ProfileRuntimeConfig(
        front_mode=config.front_mode,
        front_style=config.front_style,
        history_limit=max(1, history_limit)
        if history_limit is not None
        else config.history_limit,
        brain_model=brain_model,
        vision=config.vision,
        speech=config.speech,
        speech_input=config.speech_input,
    )
