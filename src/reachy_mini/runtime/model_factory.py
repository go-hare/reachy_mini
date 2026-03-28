"""Model factory helpers for the runtime."""

from __future__ import annotations

import json
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
        if "## user_turn_response" in prompt:
            return self._render_user_turn_response(prompt)
        if "## idle_tool_decision" in prompt:
            return self._render_idle_tool_decision(prompt)
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
    def _render_idle_tool_decision(cls, prompt: str) -> str:
        idle_tick_count_text = cls._extract_bullet_value(prompt, "idle_tick_count")
        try:
            idle_tick_count = max(int(idle_tick_count_text), 1)
        except (TypeError, ValueError):
            idle_tick_count = 1

        decisions: list[dict[str, Any]] = []
        if "move_head" in prompt:
            decisions.extend(
                [
                    {
                        "tool_name": "move_head",
                        "arguments": {"direction": "left"},
                        "reason": "light idle look-around",
                    },
                    {
                        "tool_name": "move_head",
                        "arguments": {"direction": "right"},
                        "reason": "light idle look-around",
                    },
                ]
            )
        if "do_nothing" in prompt:
            decisions.append(
                {
                    "tool_name": "do_nothing",
                    "arguments": {"reason": "quiet idle hold"},
                    "reason": "quiet idle hold",
                }
            )
        if "play_emotion" in prompt:
            decisions.append(
                {
                    "tool_name": "play_emotion",
                    "arguments": {"emotion": "random"},
                    "reason": "small expressive idle beat",
                }
            )
        if "move_head" in prompt:
            decisions.append(
                {
                    "tool_name": "move_head",
                    "arguments": {"direction": "front"},
                    "reason": "recenter after idle motion",
                }
            )
        if "do_nothing" in prompt:
            decisions.append(
                {
                    "tool_name": "do_nothing",
                    "arguments": {"reason": "settle before the next idle action"},
                    "reason": "settle before the next idle action",
                }
            )
        if "dance" in prompt:
            decisions.append(
                {
                    "tool_name": "dance",
                    "arguments": {"move": "random", "repeat": 1},
                    "reason": "brief playful idle accent",
                }
            )
        if decisions:
            return json.dumps(
                decisions[(idle_tick_count - 1) % len(decisions)],
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "tool_name": "do_nothing",
                "arguments": {"reason": "quiet idle hold"},
                "reason": "quiet idle hold",
            },
            ensure_ascii=False,
        )

    @classmethod
    def _render_user_turn_response(cls, prompt: str) -> str:
        user_text = cls._extract_section(prompt, "当前用户输入") or cls._extract_user_text(prompt)
        normalized = str(user_text or "").strip().lower()

        def _tool_available(tool_name: str) -> bool:
            return f"- {tool_name}:" in prompt

        complete_turn = False
        tool_calls: list[dict[str, Any]] = []
        reply_text = ""
        reason = "needs kernel follow-up or no front tool is necessary"

        if any(keyword in normalized for keyword in ("停止跳舞", "stop dance")) and _tool_available("stop_dance"):
            complete_turn = True
            reason = "user explicitly asked to stop dancing"
            tool_calls = [{"tool_name": "stop_dance", "arguments": {}, "reason": reason}]
        elif any(keyword in normalized for keyword in ("停止表情", "stop emotion")) and _tool_available("stop_emotion"):
            complete_turn = True
            reason = "user explicitly asked to stop the current emotion"
            tool_calls = [{"tool_name": "stop_emotion", "arguments": {}, "reason": reason}]
        elif any(keyword in normalized for keyword in ("左", "left")) and _tool_available("move_head"):
            complete_turn = True
            reason = "user asked the robot to look left"
            tool_calls = [{"tool_name": "move_head", "arguments": {"direction": "left"}, "reason": reason}]
        elif any(keyword in normalized for keyword in ("右", "right")) and _tool_available("move_head"):
            complete_turn = True
            reason = "user asked the robot to look right"
            tool_calls = [{"tool_name": "move_head", "arguments": {"direction": "right"}, "reason": reason}]
        elif any(keyword in normalized for keyword in ("上", "up")) and _tool_available("move_head"):
            complete_turn = True
            reason = "user asked the robot to look up"
            tool_calls = [{"tool_name": "move_head", "arguments": {"direction": "up"}, "reason": reason}]
        elif any(keyword in normalized for keyword in ("下", "down")) and _tool_available("move_head"):
            complete_turn = True
            reason = "user asked the robot to look down"
            tool_calls = [{"tool_name": "move_head", "arguments": {"direction": "down"}, "reason": reason}]
        elif any(keyword in normalized for keyword in ("前面", "向前", "front")) and _tool_available("move_head"):
            complete_turn = True
            reason = "user asked the robot to face front"
            tool_calls = [{"tool_name": "move_head", "arguments": {"direction": "front"}, "reason": reason}]
        elif any(keyword in normalized for keyword in ("跳舞", "dance")) and _tool_available("dance"):
            complete_turn = True
            reason = "user asked for a visible dance action"
            tool_calls = [{"tool_name": "dance", "arguments": {"move": "random", "repeat": 1}, "reason": reason}]
        elif any(keyword in normalized for keyword in ("表情", "开心", "难过", "生气", "emotion")) and _tool_available("play_emotion"):
            complete_turn = True
            reason = "user asked for an expressive emotion action"
            tool_calls = [{"tool_name": "play_emotion", "arguments": {"emotion": "random"}, "reason": reason}]
        elif any(keyword in normalized for keyword in ("跟踪", "tracking")) and _tool_available("head_tracking"):
            complete_turn = True
            start = not any(keyword in normalized for keyword in ("关闭", "停止", "off", "stop"))
            reason = "user explicitly toggled head tracking"
            tool_calls = [{"tool_name": "head_tracking", "arguments": {"start": start}, "reason": reason}]
        elif any(keyword in normalized for keyword in ("你看到了什么", "你看到啥", "what do you see", "look around")) and _tool_available("camera"):
            complete_turn = True
            reason = "user asked for a direct visual observation"
            tool_calls = [{"tool_name": "camera", "arguments": {"question": user_text}, "reason": reason}]
        else:
            if cls._needs_verification(user_text):
                reply_text = f"我先帮你看一下{user_text}，看完马上回来跟你说。"
            elif user_text:
                reply_text = f"我先接住这轮，接着继续处理“{user_text}”。"
            else:
                reply_text = "我先接着继续处理。"

        return json.dumps(
            {
                "complete_turn": complete_turn,
                "reply_text": reply_text,
                "tool_calls": tool_calls,
                "reason": reason,
            },
            ensure_ascii=False,
        )

    @classmethod
    def _extract_user_text(cls, prompt: str) -> str:
        user_text = (
            cls._extract_section(prompt, "当前用户输入")
            or cls._extract_section(prompt, "user_text")
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
    def _extract_bullet_value(prompt: str, key: str) -> str:
        marker = f"- {key}:"
        for line in prompt.splitlines():
            if line.strip().startswith(marker):
                return line.split(":", 1)[1].strip()
        return ""

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
