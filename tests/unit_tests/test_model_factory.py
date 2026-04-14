"""Tests for runtime model construction."""

from unittest.mock import patch

import pytest

from reachy_mini.runtime.config import BrainModelConfig
from reachy_mini.runtime.model_factory import build_brain_model


def test_build_brain_model_uses_inline_api_key() -> None:
    """OpenAI-backed brain models should use the inline config api_key."""
    config = BrainModelConfig(
        provider="openai",
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        api_key="brain-secret",
        temperature=0.2,
    )

    with patch("reachy_mini.runtime.model_factory.ChatOpenAI") as chat_openai:
        build_brain_model(config)

    chat_openai.assert_called_once_with(
        model="deepseek-chat",
        temperature=0.2,
        api_key="brain-secret",
        base_url="https://api.deepseek.com/v1",
    )


def test_build_brain_model_requires_inline_api_key_for_openai() -> None:
    """OpenAI-backed brain models should fail fast without api_key."""
    config = BrainModelConfig(
        provider="openai",
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        api_key="",
        temperature=0.2,
    )

    with pytest.raises(RuntimeError, match="requires `api_key`"):
        build_brain_model(config)
