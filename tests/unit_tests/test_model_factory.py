"""Tests for runtime model construction."""

from unittest.mock import patch

import pytest

from reachy_mini.runtime.config import FrontModelConfig
from reachy_mini.runtime.model_factory import build_front_model


def test_build_front_model_uses_inline_api_key() -> None:
    """OpenAI-backed front models should use the inline config api_key."""
    config = FrontModelConfig(
        provider="openai",
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        api_key="front-secret",
        temperature=0.4,
    )

    with patch("reachy_mini.runtime.model_factory.ChatOpenAI") as chat_openai:
        build_front_model(config)

    chat_openai.assert_called_once_with(
        model="deepseek-chat",
        temperature=0.4,
        api_key="front-secret",
        base_url="https://api.deepseek.com/v1",
    )


def test_build_front_model_requires_inline_api_key_for_openai() -> None:
    """OpenAI-backed front models should fail fast without api_key."""
    config = FrontModelConfig(
        provider="openai",
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        api_key="",
        temperature=0.4,
    )

    with pytest.raises(RuntimeError, match="requires `api_key`"):
        build_front_model(config)
