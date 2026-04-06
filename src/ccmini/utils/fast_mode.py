"""Fast mode — switch between fast and quality models per-task.

``FastModeManager`` tracks whether the session is in *fast* or *quality*
mode and provides helpers to auto-switch based on context token count.

Configuration is loaded from ``~/.mini_agent/fast_mode.json`` (or via
the ``MINI_AGENT_FAST_MODE`` env var) and exposed through
:class:`FastModeConfig`.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from ..paths import mini_agent_home

log = logging.getLogger(__name__)

_CONFIG_DIR = mini_agent_home()
_CONFIG_FILE = _CONFIG_DIR / "fast_mode.json"


class ModelTier(str, Enum):
    FAST = "fast"
    QUALITY = "quality"


# Mapping from task type → preferred tier
_DEFAULT_TASK_MODEL: dict[str, ModelTier] = {
    "summarize": ModelTier.FAST,
    "code_edit": ModelTier.QUALITY,
    "search": ModelTier.FAST,
    "plan": ModelTier.QUALITY,
    "refactor": ModelTier.QUALITY,
    "review": ModelTier.QUALITY,
    "chat": ModelTier.FAST,
}


@dataclass(slots=True)
class FastModeConfig:
    """Persisted fast-mode settings."""

    fast_model: str = "claude-3-5-haiku-latest"
    quality_model: str = "claude-sonnet-4-20250514"
    auto_switch: bool = True
    switch_threshold_tokens: int = 80_000
    task_model_overrides: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> FastModeConfig:
        """Load from ``~/.mini_agent/fast_mode.json``, falling back to defaults."""
        try:
            if _CONFIG_FILE.exists():
                data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            log.debug("Could not load fast_mode config; using defaults", exc_info=True)
        return cls()

    def save(self) -> None:
        """Persist to ``~/.mini_agent/fast_mode.json``."""
        try:
            _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _CONFIG_FILE.write_text(
                json.dumps(
                    {f.name: getattr(self, f.name) for f in self.__dataclass_fields__.values()},
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            log.debug("Could not save fast_mode config", exc_info=True)


class FastModeManager:
    """Runtime state for fast/quality model switching.

    Reads the env var ``MINI_AGENT_FAST_MODE`` (``1``/``0``) on startup.
    When the env var is set, it overrides the config file default.
    """

    def __init__(self, config: FastModeConfig | None = None) -> None:
        self._config = config or FastModeConfig.load()
        self._enabled = self._resolve_initial_state()
        self._cooldown_until: float = 0.0

    def _resolve_initial_state(self) -> bool:
        env = os.environ.get("MINI_AGENT_FAST_MODE")
        if env is not None:
            return env.strip() in ("1", "true", "yes")
        return False

    @property
    def config(self) -> FastModeConfig:
        return self._config

    def is_fast_mode(self) -> bool:
        return self._enabled

    def enable_fast_mode(self) -> None:
        self._enabled = True
        log.info("Fast mode enabled (using %s)", self._config.fast_model)

    def disable_fast_mode(self) -> None:
        self._enabled = False
        log.info("Fast mode disabled (using %s)", self._config.quality_model)

    def toggle_fast_mode(self) -> bool:
        """Toggle and return the new state."""
        if self._enabled:
            self.disable_fast_mode()
        else:
            self.enable_fast_mode()
        return self._enabled

    def get_active_model(self) -> str:
        if self._enabled:
            return self._config.fast_model
        return self._config.quality_model

    def auto_switch_check(self, context_tokens: int) -> None:
        """Switch to fast model when context exceeds the threshold."""
        if not self._config.auto_switch:
            return
        if context_tokens >= self._config.switch_threshold_tokens and not self._enabled:
            log.info(
                "Auto-switching to fast mode (context %d tokens >= threshold %d)",
                context_tokens,
                self._config.switch_threshold_tokens,
            )
            self.enable_fast_mode()

    def get_model_for_task(self, task_type: str) -> str:
        """Return the appropriate model for *task_type*.

        Checks user overrides in config, then the built-in default map,
        and falls back to the active model.
        """
        override = self._config.task_model_overrides.get(task_type)
        if override:
            return override

        tier = _DEFAULT_TASK_MODEL.get(task_type)
        if tier == ModelTier.FAST:
            return self._config.fast_model
        if tier == ModelTier.QUALITY:
            return self._config.quality_model
        return self.get_active_model()

    def get_state_summary(self) -> dict[str, Any]:
        return {
            "fast_mode": self._enabled,
            "active_model": self.get_active_model(),
            "fast_model": self._config.fast_model,
            "quality_model": self._config.quality_model,
            "auto_switch": self._config.auto_switch,
            "threshold_tokens": self._config.switch_threshold_tokens,
        }


class FastModeMiddleware:
    """Hook that intercepts model selection per query.

    Register a callback via :meth:`on_model_select`; the middleware
    calls it with the computed model name and may replace it.
    """

    def __init__(self, manager: FastModeManager) -> None:
        self._manager = manager
        self._interceptors: list[Callable[[str], str | None]] = []

    def on_model_select(self, fn: Callable[[str], str | None]) -> None:
        """Register an interceptor.  Return a replacement model name or None."""
        self._interceptors.append(fn)

    def resolve_model(self, task_type: str | None = None) -> str:
        """Determine the model for the current query/task."""
        if task_type:
            model = self._manager.get_model_for_task(task_type)
        else:
            model = self._manager.get_active_model()

        for fn in self._interceptors:
            replacement = fn(model)
            if replacement is not None:
                model = replacement

        return model
