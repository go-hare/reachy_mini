"""OpenAI-compatible provider for third-party endpoints.

Supports vLLM, Ollama, DeepSeek, LM Studio, and any server that
implements the OpenAI Chat Completions API.  Inherits from
:class:`OpenAIProvider` and only overrides the client setup to use
a custom ``base_url``.
"""

from __future__ import annotations

from typing import Any

from . import ProviderConfig
from .openai import OpenAIProvider


class OpenAICompatibleProvider(OpenAIProvider):
    """OpenAI-compatible endpoint (vLLM, Ollama, DeepSeek, etc.)."""

    def __init__(self, config: ProviderConfig) -> None:
        if not config.base_url:
            raise ValueError(
                "OpenAI-compatible provider requires a base_url. "
                "Set it in ProviderConfig or config.jsonl."
            )
        super().__init__(config)

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                import openai
            except ImportError as exc:
                raise ImportError(
                    "Install the openai SDK: pip install openai"
                ) from exc
            kwargs: dict[str, Any] = {
                "base_url": self._config.base_url,
            }
            if self._config.api_key:
                kwargs["api_key"] = self._config.api_key
            else:
                kwargs["api_key"] = "not-needed"
            self._client = openai.AsyncOpenAI(**kwargs)
        return self._client
