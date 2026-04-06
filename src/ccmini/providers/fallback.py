"""Model fallback — automatic switch to a backup model on overload.

Mirrors Claude Code's FallbackTriggeredError pattern:
- Count consecutive 529/overloaded errors
- After threshold, switch to fallback model
- Notify via hooks
"""

from __future__ import annotations

import logging
from dataclasses import replace
from dataclasses import dataclass, field
from typing import Any

from . import BaseProvider, ProviderConfig, create_provider

logger = logging.getLogger(__name__)


@dataclass
class FallbackConfig:
    """Configuration for model fallback."""
    fallback_model: str = ""
    fallback_provider: str = ""
    max_consecutive_overloaded: int = 3
    enabled: bool = True


class FallbackTriggeredError(Exception):
    """Raised by the retry layer when fallback threshold is reached."""

    def __init__(self, original_model: str, fallback_model: str) -> None:
        super().__init__(
            f"Switched to {fallback_model} due to high demand for {original_model}"
        )
        self.original_model = original_model
        self.fallback_model = fallback_model


class ModelFallbackManager:
    """Tracks overloaded errors and triggers model fallback.

    Usage::

        mgr = ModelFallbackManager(config, primary_provider)

        try:
            # ... API call ...
        except SomeOverloadedError:
            mgr.record_overloaded()
            if mgr.should_fallback():
                provider = mgr.get_fallback_provider()
    """

    def __init__(
        self,
        config: FallbackConfig,
        primary_provider: BaseProvider,
    ) -> None:
        self._config = config
        self._primary = primary_provider
        self._fallback: BaseProvider | None = None
        self._consecutive_overloaded = 0
        self._is_using_fallback = False
        self._fallback_count = 0

    @property
    def is_using_fallback(self) -> bool:
        return self._is_using_fallback

    @property
    def current_model(self) -> str:
        if self._is_using_fallback and self._fallback:
            return self._fallback.model_name
        return self._primary.model_name

    @property
    def fallback_count(self) -> int:
        return self._fallback_count

    def record_overloaded(self) -> None:
        """Record a consecutive overloaded error."""
        self._consecutive_overloaded += 1
        logger.warning(
            "Overloaded error %d/%d for model %s",
            self._consecutive_overloaded,
            self._config.max_consecutive_overloaded,
            self._primary.model_name,
        )

    def record_success(self) -> None:
        """Reset the overloaded counter on success."""
        self._consecutive_overloaded = 0

    def should_fallback(self) -> bool:
        """Check if we should switch to the fallback model."""
        if not self._config.enabled or not self._config.fallback_model:
            return False
        return self._consecutive_overloaded >= self._config.max_consecutive_overloaded

    def get_fallback_provider(self) -> BaseProvider:
        """Get or create the fallback provider."""
        if self._fallback is None:
            primary_config = getattr(self._primary, "_config", None)
            if isinstance(primary_config, ProviderConfig):
                fallback_config = replace(
                    primary_config,
                    type=self._config.fallback_provider or primary_config.type,
                    model=self._config.fallback_model,
                )
            else:
                provider_type = self._config.fallback_provider or "anthropic"
                fallback_config = ProviderConfig(
                    type=provider_type,
                    model=self._config.fallback_model,
                )
            self._fallback = create_provider(fallback_config)
        self._is_using_fallback = True
        self._fallback_count += 1
        self._consecutive_overloaded = 0
        logger.info(
            "Fallback triggered: %s -> %s",
            self._primary.model_name,
            self._fallback.model_name,
        )
        return self._fallback

    def restore_primary(self) -> BaseProvider:
        """Switch back to primary model."""
        self._is_using_fallback = False
        self._consecutive_overloaded = 0
        return self._primary

    def get_active_provider(self) -> BaseProvider:
        """Get the currently active provider."""
        if self._is_using_fallback and self._fallback:
            return self._fallback
        return self._primary

    def status(self) -> dict[str, Any]:
        return {
            "primary_model": self._primary.model_name,
            "fallback_model": self._config.fallback_model,
            "using_fallback": self._is_using_fallback,
            "consecutive_overloaded": self._consecutive_overloaded,
            "fallback_count": self._fallback_count,
        }


def is_overloaded_error(exc: BaseException) -> bool:
    """Detect 529/overloaded errors across providers."""
    status = _get_status(exc)
    if status == 529:
        return True
    msg = str(exc).lower()
    return "overloaded" in msg or "overloaded_error" in msg


def _get_status(exc: BaseException) -> int:
    for attr in ("status_code", "status", "code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    response = getattr(exc, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None) or getattr(response, "status", None)
        if isinstance(code, int):
            return code
    return 0
