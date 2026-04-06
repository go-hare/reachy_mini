"""LLM provider abstraction layer.

Each provider translates between mini-agent's internal message format
and the specific wire format of an LLM API (Anthropic Messages,
OpenAI Chat Completions, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ..messages import ContentBlock, Message, StreamEvent
from .retry import RetryConfig
from ..tool import Tool


@dataclass(slots=True)
class ProviderConfig:
    """Configuration for creating a provider instance."""
    type: str                       # "anthropic" | "openai" | "compatible"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 8192
    temperature: float = 0.7
    retry: RetryConfig = RetryConfig()
    enable_cache: bool = True
    extras: dict[str, Any] = field(default_factory=dict)


class BaseProvider(ABC):
    """Abstract base for all LLM providers."""

    @abstractmethod
    async def stream(
        self,
        *,
        messages: list[Message],
        system: str | list[dict[str, Any]] = "",
        tools: list[Tool] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        task_budget: dict[str, int] | None = None,
        query_source: str = "",
        stop_sequences: list[str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Send a request and yield stream events.

        Implementations must convert internal Messages/Tools to the
        provider's wire format, stream the response, and yield
        mini-agent StreamEvents.
        """
        ...  # pragma: no cover

    @abstractmethod
    async def complete(
        self,
        *,
        messages: list[Message],
        system: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
        query_source: str = "",
        stop_sequences: list[str] | None = None,
        tools: list[Tool] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> Message:
        """Non-streaming single-shot completion (used by compaction, side queries).

        ``stop_sequences`` mirrors Claude Code ``sideQuery`` ``stop_sequences`` /
        OpenAI ``stop`` where supported.

        ``tools`` / ``tool_choice`` are passed through to the provider when set
        (forced tool use, structured tool-only completions, etc.).
        """
        ...  # pragma: no cover

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The model identifier string."""
        ...  # pragma: no cover


def create_provider(config: ProviderConfig) -> BaseProvider:
    """Factory: create a provider from config."""
    match config.type:
        case "mock":
            from .mock import MockProvider
            return MockProvider(config)
        case "anthropic":
            from .anthropic import AnthropicProvider
            return AnthropicProvider(config)
        case "openai":
            from .openai import OpenAIProvider
            return OpenAIProvider(config)
        case "compatible" | "ollama" | "vllm" | "deepseek":
            from .compatible import OpenAICompatibleProvider
            return OpenAICompatibleProvider(config)
        case _:
            raise ValueError(
                f"Unknown provider type: {config.type!r}. "
                f"Supported: 'mock', 'anthropic', 'openai', 'compatible', 'ollama', 'vllm', 'deepseek'"
            )
