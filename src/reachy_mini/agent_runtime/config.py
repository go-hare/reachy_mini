"""Profile runtime configuration for the text runtime."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from reachy_mini.agent_runtime.profile_loader import ProfileWorkspace


@dataclass(slots=True)
class FrontModelConfig:
    """How the front layer should talk to a model."""

    provider: str = "mock"
    model: str = "reachy_mini_front_mock"
    base_url: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    temperature: float = 0.4


@dataclass(slots=True)
class KernelModelConfig:
    """How the kernel layer should talk to a model."""

    provider: str = "mock"
    model: str = "reachy_mini_kernel_mock"
    base_url: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    temperature: float = 0.2


@dataclass(slots=True)
class AgentProfileConfig:
    """Structured runtime settings parsed from ``config.jsonl``."""

    front_mode: str = "text"
    front_style: str = "friendly_concise"
    history_limit: int = 6
    front_model: FrontModelConfig = field(default_factory=FrontModelConfig)
    kernel_model: KernelModelConfig = field(default_factory=KernelModelConfig)


def load_agent_profile_config(profile: ProfileWorkspace) -> AgentProfileConfig:
    """Load stage-2 runtime settings from a profile workspace."""
    config = AgentProfileConfig()

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
                api_key_env=str(
                    record.get("api_key_env", config.front_model.api_key_env)
                    or config.front_model.api_key_env
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
                api_key_env=str(
                    record.get("api_key_env", config.kernel_model.api_key_env)
                    or config.kernel_model.api_key_env
                ),
                temperature=float(
                    record.get("temperature", config.kernel_model.temperature)
                ),
            )

    return config


def apply_runtime_overrides(
    config: AgentProfileConfig,
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    temperature: float | None = None,
    kernel_provider: str | None = None,
    kernel_model: str | None = None,
    kernel_base_url: str | None = None,
    kernel_api_key_env: str | None = None,
    kernel_temperature: float | None = None,
    history_limit: int | None = None,
) -> AgentProfileConfig:
    """Apply CLI overrides on top of a parsed profile config."""
    front_model = replace(
        config.front_model,
        provider=provider or config.front_model.provider,
        model=model or config.front_model.model,
        base_url=base_url if base_url is not None else config.front_model.base_url,
        api_key_env=(
            api_key_env if api_key_env is not None else config.front_model.api_key_env
        ),
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
        api_key_env=(
            kernel_api_key_env
            if kernel_api_key_env is not None
            else config.kernel_model.api_key_env
        ),
        temperature=kernel_temperature
        if kernel_temperature is not None
        else config.kernel_model.temperature,
    )
    return AgentProfileConfig(
        front_mode=config.front_mode,
        front_style=config.front_style,
        history_limit=max(1, history_limit)
        if history_limit is not None
        else config.history_limit,
        front_model=front_model,
        kernel_model=resolved_kernel_model,
    )
