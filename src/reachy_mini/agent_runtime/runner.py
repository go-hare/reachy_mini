"""Front-plus-kernel runtime runner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from reachy_mini.agent_core import (
    BrainKernel,
    BrainResponse,
    JsonlMemoryStore,
    TaskType,
    make_id,
)
from reachy_mini.agent_core.memory import MemoryView
from reachy_mini.agent_runtime.config import AgentProfileConfig
from reachy_mini.agent_runtime.profile_loader import ProfileWorkspace
from reachy_mini.agent_runtime.session_store import FrontSessionStore
from reachy_mini.agent_runtime.model_factory import build_front_model, build_kernel_model
from reachy_mini.front import FrontService


def _build_kernel_system_prompt(profile: ProfileWorkspace) -> str:
    """Compile the profile workspace files into one kernel system prompt."""
    sections = [profile.agents_md.strip()]
    if profile.user_md.strip():
        sections.append(f"## USER\n{profile.user_md.strip()}")
    if profile.soul_md.strip():
        sections.append(f"## SOUL\n{profile.soul_md.strip()}")
    if profile.tools_md.strip():
        sections.append(f"## TOOLS\n{profile.tools_md.strip()}")
    return "\n\n".join(section for section in sections if section).strip()


class FrontAgentRunner:
    """Drive one profile workspace through the text runtime."""

    def __init__(
        self,
        *,
        profile: ProfileWorkspace,
        config: AgentProfileConfig,
        front: FrontService | None = None,
        kernel: BrainKernel | None = None,
    ):
        """Create the runtime objects for one loaded profile."""
        self.profile = profile
        self.config = config
        self.session_store = FrontSessionStore(profile)
        self.front = front or FrontService(profile, build_front_model(config.front_model))
        self.kernel = kernel

    @classmethod
    def from_profile(
        cls,
        *,
        profile: ProfileWorkspace,
        config: AgentProfileConfig,
        enable_kernel: bool = True,
    ) -> "FrontAgentRunner":
        """Build a runner directly from one loaded profile workspace."""
        front = FrontService(profile, build_front_model(config.front_model))
        kernel = None
        if enable_kernel:
            kernel_model = build_kernel_model(config.kernel_model)
            kernel = BrainKernel(
                agent_id=profile.name,
                model=kernel_model,
                task_router_model=kernel_model,
                memory_store=JsonlMemoryStore(profile.root),
                system_prompt=_build_kernel_system_prompt(profile),
            )
        return cls(profile=profile, config=config, front=front, kernel=kernel)

    async def reply(
        self,
        *,
        thread_id: str,
        user_text: str,
        stream_handler: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Generate one reply and persist dialogue plus kernel records."""
        memory = self.session_store.build_memory_view(
            thread_id=thread_id,
            limit=self.config.history_limit,
        )
        if self.kernel is None:
            reply = await self.front.reply(
                user_text=user_text,
                memory=memory,
                style=self.config.front_style,
                stream_handler=stream_handler,
            )
            self.session_store.append_dialogue(
                thread_id=thread_id,
                role="user",
                content=user_text,
            )
            self.session_store.append_dialogue(
                thread_id=thread_id,
                role="assistant",
                content=reply,
            )
            return reply

        front_memory = self._build_kernel_memory(thread_id=thread_id, user_text=user_text)
        front_reply = await self.front.reply(
            user_text=user_text,
            memory=front_memory,
            style=self.config.front_style,
        )
        turn_id = make_id("turn")
        await self.kernel.handle_front_event(
            conversation_id=thread_id,
            turn_id=turn_id,
            front_event={
                "event_type": "dialogue",
                "user_text": user_text,
                "front_reply": front_reply,
            },
        )
        response = await self.kernel.handle_user_input(
            conversation_id=thread_id,
            text=user_text,
            turn_id=turn_id,
            latest_front_reply=front_reply,
        )
        kernel_output = self._render_kernel_response(response)
        if not kernel_output:
            return front_reply

        presentation_memory = self._build_kernel_memory(
            thread_id=thread_id,
            user_text=user_text,
        )
        reply = await self.front.present(
            user_text=user_text,
            kernel_output=kernel_output,
            memory=presentation_memory,
            style=self.config.front_style,
            stream_handler=stream_handler,
        )
        return reply

    def _build_kernel_memory(self, *, thread_id: str, user_text: str) -> MemoryView:
        """Build the brain-kernel-backed memory view for front usage."""
        if self.kernel is None or self.kernel.memory_store is None:
            return self.session_store.build_memory_view(
                thread_id=thread_id,
                limit=self.config.history_limit,
            )
        return self.kernel.memory_store.build_memory_view(
            thread_id,
            self.kernel.agent_id,
            user_text,
            limit=self.config.history_limit,
        )

    @staticmethod
    def _render_kernel_response(response: BrainResponse) -> str:
        """Normalize a brain response into the raw text shown to front."""
        if response.task_type == TaskType.none:
            return ""

        reply = str(response.reply or "").strip()
        if reply:
            return reply

        if response.pending_tool_calls:
            tool_names = ", ".join(
                item.tool_name
                for item in response.pending_tool_calls
                if str(item.tool_name or "").strip()
            ).strip()
            if tool_names:
                return f"需要继续等待这些工具结果后再往下：{tool_names}"
            return "需要继续等待工具结果后再往下。"

        if response.run is not None:
            return str(response.run.result_summary or "").strip()
        return ""
