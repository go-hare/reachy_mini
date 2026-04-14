"""Helpers for building the single-brain ccmini runtime host."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ccmini import create_robot_agent
from ccmini.agent import Agent
from ccmini.messages import CompletionEvent, Message, StreamEvent, TextEvent, assistant_message
from ccmini.providers import BaseProvider, ProviderConfig
from ccmini.tool import FunctionTool, Tool as CcminiTool

from reachy_mini.runtime.tool_loader import RuntimeToolBundle, build_runtime_tool_bundle
from reachy_mini.runtime.tools import ReachyToolContext

if False:  # pragma: no cover
    from reachy_mini.runtime.config import ProfileRuntimeConfig
    from reachy_mini.runtime.profile_loader import ProfileBundle


@dataclass(frozen=True, slots=True)
class RuntimeAgentBundle:
    """Single-brain runtime dependencies assembled from one profile."""

    agent: Agent
    tool_bundle: RuntimeToolBundle
    tool_names: tuple[str, ...]


def build_runtime_agent_bundle(
    *,
    profile: "ProfileBundle",
    config: "ProfileRuntimeConfig",
    runtime_context: ReachyToolContext | None = None,
) -> RuntimeAgentBundle:
    """Build one single-brain ccmini agent plus its resolved tool bundle."""

    tool_bundle = build_runtime_tool_bundle(
        profile,
        runtime_context=runtime_context,
    )
    tools = _build_ccmini_tools(tool_bundle)
    tool_names = tuple(tool.name for tool in tools)
    agent = create_robot_agent(
        provider=_build_provider(config),
        system_prompt=_build_single_brain_system_prompt(
            profile,
            workspace_root=tool_bundle.workspace_root,
            tool_names=tool_names,
            front_style=str(getattr(config, "front_style", "") or ""),
        ),
        tools=tools,
        agent_id=profile.name,
        use_default_tools=False,
    )
    agent.set_memory_roots(profile_root=str(profile.root))
    return RuntimeAgentBundle(
        agent=agent,
        tool_bundle=tool_bundle,
        tool_names=tool_names,
    )


def _build_provider(config: "ProfileRuntimeConfig") -> BaseProvider | ProviderConfig:
    """Map runtime model settings into one ccmini provider instance or config."""

    brain = config.brain_model
    provider = str(getattr(brain, "provider", "") or "mock").strip().lower()
    if provider == "mock":
        return ReachyRuntimeMockProvider()
    provider_type = {
        "openai": "openai",
        "ollama": "ollama",
        "compatible": "compatible",
        "deepseek": "compatible",
        "vllm": "compatible",
    }.get(provider, provider or "openai")
    return ProviderConfig(
        type=provider_type,
        model=str(getattr(brain, "model", "") or "").strip(),
        api_key=str(getattr(brain, "api_key", "") or "").strip(),
        base_url=str(getattr(brain, "base_url", "") or "").strip(),
        temperature=float(getattr(brain, "temperature", 0.2) or 0.2),
    )


def _build_single_brain_system_prompt(
    profile: "ProfileBundle",
    *,
    workspace_root: Path,
    tool_names: tuple[str, ...],
    front_style: str,
) -> str:
    """Compile one single system prompt from the profile assets."""

    sections = [profile.agents_md.strip()]
    if profile.user_md.strip():
        sections.append(f"## USER\n{profile.user_md.strip()}")
    if profile.soul_md.strip():
        sections.append(f"## SOUL\n{profile.soul_md.strip()}")
    if profile.tools_md.strip():
        sections.append(f"## TOOLS\n{profile.tools_md.strip()}")
    if profile.front_md.strip():
        sections.append(f"## FRONT\n{profile.front_md.strip()}")

    runtime_lines = [
        "## RUNTIME",
        f"- Workspace root: {workspace_root}",
        "- You are the only cognitive layer in the Reachy Mini runtime.",
        "- Reply for the user directly; there is no separate front-model polish step.",
        "- The host owns UI phases, audio playback, motion safety, and browser protocol mapping.",
        "- Use tools when the task needs filesystem, robot motion, camera, or other host abilities.",
        "- Never claim a tool or file change succeeded unless the tool result confirms it.",
    ]
    if front_style:
        runtime_lines.append(f"- Preferred reply style: {front_style}")
    if tool_names:
        runtime_lines.append(f"- Available runtime tools: {', '.join(tool_names)}")
    sections.append("\n".join(runtime_lines))
    return "\n\n".join(section for section in sections if section).strip()


def _build_ccmini_tools(tool_bundle: RuntimeToolBundle) -> list[CcminiTool]:
    """Adapt the legacy runtime tool bundle into ccmini tools."""

    wrapped: list[CcminiTool] = []
    seen_names: set[str] = set()
    legacy_tools = [
        *tool_bundle.kernel_system_tools,
        *tool_bundle.front_tools,
        *tool_bundle.profile_tools,
    ]
    for tool in legacy_tools:
        adapted = _adapt_tool(tool)
        if adapted is None or adapted.name in seen_names:
            continue
        wrapped.append(adapted)
        seen_names.add(adapted.name)
    return wrapped


def _adapt_tool(tool: Any) -> CcminiTool | None:
    """Adapt one runtime tool object into the ccmini Tool contract."""

    if isinstance(tool, CcminiTool):
        return tool

    name = str(getattr(tool, "name", "") or "").strip()
    description = str(getattr(tool, "description", "") or "").strip()
    if not name or not description or not hasattr(tool, "execute"):
        return None

    parameters = getattr(tool, "parameters", None)
    is_read_only = name in {
        "read_file",
        "list_dir",
        "search_files",
        "camera",
    }

    async def _execute(**kwargs: Any) -> str:
        result = tool.execute(**kwargs)
        if hasattr(result, "__await__"):
            result = await result
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)

    return FunctionTool(
        name=name,
        description=description,
        func=_execute,
        parameters=dict(parameters or {}),
        is_read_only=is_read_only,
    )


class ReachyRuntimeMockProvider(BaseProvider):
    """Deterministic mock replies tailored for the Reachy runtime."""

    @property
    def model_name(self) -> str:
        return "reachy-runtime-mock"

    async def stream(
        self,
        *,
        messages: list[Message],
        system: str | list[dict[str, Any]] = "",
        tools: list[CcminiTool] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        task_budget: dict[str, int] | None = None,
        query_source: str = "",
        stop_sequences: list[str] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        _ = system, tools, max_tokens, temperature, task_budget, query_source, stop_sequences
        reply = self._build_reply(messages)
        yield TextEvent(text=reply)
        yield CompletionEvent(text=reply, stop_reason="end_turn")

    async def complete(
        self,
        *,
        messages: list[Message],
        system: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
        query_source: str = "",
        stop_sequences: list[str] | None = None,
        tools: list[CcminiTool] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> Message:
        _ = system, max_tokens, temperature, query_source, stop_sequences, tools, tool_choice
        return assistant_message(self._build_reply(messages))

    def _build_reply(self, messages: list[Message]) -> str:
        user_text = self._extract_user_text(messages)
        lowered = user_text.lower()
        if any(keyword in lowered for keyword in ("看", "检查", "日志", "文件", "read", "check", "log", "file")):
            return f"需要先查看和“{user_text}”相关的文件或日志，确认后才能给你准确结论。"
        if not user_text:
            return "我在，告诉我下一步想让我做什么。"
        return f"收到，我会围绕“{user_text}”继续处理。"

    @staticmethod
    def _extract_user_text(messages: list[Message]) -> str:
        for message in reversed(messages):
            if getattr(message, "role", "") != "user":
                continue
            text = str(message.text or "").strip()
            if not text:
                continue
            if "</system-reminder>" in text:
                text = text.split("</system-reminder>", 1)[1].strip()
            if "# Companion" in text:
                text = text.split("# Companion", 1)[0].strip()
            if text:
                return text
        return ""
