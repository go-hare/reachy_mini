"""User-facing front service for the stage-2 runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from reachy_mini.agent_runtime.front_prompt import FrontPromptBuilder
from reachy_mini.agent_runtime.memory import MemoryView
from reachy_mini.agent_runtime.message_utils import extract_message_text
from reachy_mini.agent_runtime.profile_loader import ProfileWorkspace


class FrontService:
    """Fast text-only front layer driven by a profile workspace."""

    def __init__(self, profile: ProfileWorkspace, model: Any):
        """Store the profile workspace and chat model."""
        self.profile = profile
        self.model = model
        self.prompts = FrontPromptBuilder()

    async def reply(
        self,
        *,
        user_text: str,
        memory: MemoryView,
        style: str,
        stream_handler: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Generate the user-visible front reply."""
        system_text = self._front_system_text()
        user_prompt = self.prompts.build_user_prompt(
            user_text=user_text,
            memory=memory,
            style=style,
        )
        messages = [SystemMessage(content=system_text), HumanMessage(content=user_prompt)]
        return await self.run(messages, stream_handler)

    async def run(
        self,
        messages: list[SystemMessage | HumanMessage],
        stream_handler: Callable[[str], Awaitable[None]] | None,
    ) -> str:
        """Run the configured model and normalize the reply text."""
        if stream_handler is not None and hasattr(self.model, "astream"):
            full_text = ""
            async for chunk in self.model.astream(messages):
                text = extract_message_text(chunk)
                if not text:
                    continue
                full_text += text
                await stream_handler(text)
            return full_text.strip()

        if hasattr(self.model, "ainvoke"):
            response = await self.model.ainvoke(messages)
            return extract_message_text(response)

        if hasattr(self.model, "invoke"):
            response = self.model.invoke(messages)
            return extract_message_text(response)

        raise RuntimeError("Front model does not support invoke or astream")

    def _front_system_text(self) -> str:
        sections = [self.profile.front_md.strip()]
        if self.profile.agents_md.strip():
            sections.append(f"## AGENTS\n{self.profile.agents_md.strip()}")
        return "\n\n".join(section for section in sections if section).strip()
