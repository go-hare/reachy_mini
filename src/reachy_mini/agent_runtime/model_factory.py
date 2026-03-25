"""Front model factory for the stage-2 runtime."""

from __future__ import annotations

import os
import re
from typing import Any

from langchain_core.messages import AIMessage
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from reachy_mini.agent_runtime.config import FrontModelConfig
from reachy_mini.agent_runtime.message_utils import extract_message_text


class MockFrontModel:
    """Deterministic local fallback for the front layer."""

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        """Return one async mock response."""
        return AIMessage(content=self._render_reply(messages))

    def invoke(self, messages: list[Any]) -> AIMessage:
        """Return one sync mock response."""
        return AIMessage(content=self._render_reply(messages))

    def _render_reply(self, messages: list[Any]) -> str:
        prompt = extract_message_text(messages[-1]) if messages else ""
        user_text = self._extract_user_text(prompt)
        if self._needs_verification(user_text):
            return f"我先帮你看一下{user_text}，看完马上回来跟你说。"
        if user_text:
            return f"我在，我们就顺着“{user_text}”继续往下聊。"
        return "我在，我们继续。"

    @staticmethod
    def _extract_user_text(prompt: str) -> str:
        match = re.search(r"## 当前用户输入\s*(.+)$", prompt, re.DOTALL)
        if match is None:
            return prompt.strip().splitlines()[-1].strip() if prompt.strip() else ""
        return match.group(1).strip()

    @staticmethod
    def _needs_verification(user_text: str) -> bool:
        lowered = str(user_text or "").lower()
        return any(
            keyword in lowered
            for keyword in (
                "读",
                "看",
                "检查",
                "分析",
                "文件",
                "代码",
                "日志",
                "read ",
                "check ",
                "file",
                "code",
                "log",
            )
        )


def build_front_model(config: FrontModelConfig) -> Any:
    """Build the configured front model."""
    provider = str(config.provider or "mock").strip().lower()
    if provider == "mock":
        return MockFrontModel()

    if provider == "openai":
        api_key = os.getenv(config.api_key_env, "").strip() if config.api_key_env else ""
        if not api_key:
            raise RuntimeError(
                f"Front provider 'openai' requires the env var {config.api_key_env!r}."
            )
        if not config.model.strip():
            raise RuntimeError("Front provider 'openai' requires a model name.")
        kwargs: dict[str, Any] = {
            "model": config.model,
            "temperature": config.temperature,
            "api_key": api_key,
        }
        if config.base_url.strip():
            kwargs["base_url"] = config.base_url.strip()
        return ChatOpenAI(**kwargs)

    if provider == "ollama":
        if not config.model.strip():
            raise RuntimeError("Front provider 'ollama' requires a model name.")
        kwargs: dict[str, Any] = {
            "model": config.model,
            "temperature": config.temperature,
        }
        if config.base_url.strip():
            kwargs["base_url"] = config.base_url.strip()
        return ChatOllama(**kwargs)

    raise RuntimeError(f"Unsupported front provider: {config.provider}")
