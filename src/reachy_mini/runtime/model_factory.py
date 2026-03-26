"""Model factory helpers for the runtime."""

from __future__ import annotations
from typing import Any

from langchain_core.messages import AIMessage
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from reachy_mini.core.message_utils import extract_message_text
from reachy_mini.runtime.config import FrontModelConfig, KernelModelConfig


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
        kernel_output = (
            self._extract_section(prompt, "后台主脑原始结果")
            or self._extract_section(prompt, "Kernel 原始输出")
        )
        if kernel_output:
            return kernel_output
        user_text = self._extract_user_text(prompt)
        if self._needs_verification(user_text):
            return f"我先帮你看一下{user_text}，看完马上回来跟你说。"
        if user_text:
            return f"我在，我们就顺着“{user_text}”继续往下聊。"
        return "我在，我们继续。"

    @classmethod
    def _extract_user_text(cls, prompt: str) -> str:
        user_text = (
            cls._extract_section(prompt, "当前用户输入")
            or cls._extract_section(prompt, "用户输入")
            or cls._extract_section(prompt, "用户原始输入")
        )
        if not user_text:
            return prompt.strip().splitlines()[-1].strip() if prompt.strip() else ""
        return user_text

    @staticmethod
    def _extract_section(prompt: str, title: str) -> str:
        marker = f"## {title}"
        start = prompt.find(marker)
        if start < 0:
            return ""
        remainder = prompt[start + len(marker) :].lstrip()
        next_header = remainder.find("\n## ")
        if next_header >= 0:
            remainder = remainder[:next_header]
        return remainder.strip()

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


class MockKernelModel:
    """Deterministic local fallback for the kernel layer."""

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        """Return one async mock response."""
        return AIMessage(content=self._render_reply(messages))

    def invoke(self, messages: list[Any]) -> AIMessage:
        """Return one sync mock response."""
        return AIMessage(content=self._render_reply(messages))

    def _render_reply(self, messages: list[Any]) -> str:
        prompt = extract_message_text(messages[-1]) if messages else ""
        user_text = MockFrontModel._extract_user_text(prompt)
        if self._needs_verification(user_text):
            return f"需要先查看和“{user_text}”相关的文件或日志，确认后才能给你准确结论。"
        if user_text:
            return f"先围绕“{user_text}”继续推进，我会给你明确的下一步。"
        return "请再给我一点上下文，我再继续推进。"

    @staticmethod
    def _needs_verification(user_text: str) -> bool:
        """Detect requests that need inspection before answering."""
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


def _build_remote_model(
    config: FrontModelConfig | KernelModelConfig,
    *,
    layer_name: str,
) -> Any:
    """Build one remote-backed chat model."""
    provider = str(config.provider or "mock").strip().lower()
    if provider == "openai":
        api_key = str(config.api_key or "").strip()
        if not api_key:
            raise RuntimeError(f"{layer_name} provider 'openai' requires `api_key`.")
        if not config.model.strip():
            raise RuntimeError(f"{layer_name} provider 'openai' requires a model name.")
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
            raise RuntimeError(f"{layer_name} provider 'ollama' requires a model name.")
        kwargs: dict[str, Any] = {
            "model": config.model,
            "temperature": config.temperature,
        }
        if config.base_url.strip():
            kwargs["base_url"] = config.base_url.strip()
        return ChatOllama(**kwargs)

    raise RuntimeError(f"Unsupported {layer_name.lower()} provider: {config.provider}")


def build_front_model(config: FrontModelConfig) -> Any:
    """Build the configured front model."""
    if str(config.provider or "mock").strip().lower() == "mock":
        return MockFrontModel()
    return _build_remote_model(config, layer_name="Front")


def build_kernel_model(config: KernelModelConfig) -> Any:
    """Build the configured kernel model."""
    if str(config.provider or "mock").strip().lower() == "mock":
        return MockKernelModel()
    return _build_remote_model(config, layer_name="Kernel")
