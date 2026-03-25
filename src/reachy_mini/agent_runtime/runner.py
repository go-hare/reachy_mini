"""Stage-2 front-only runtime runner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from reachy_mini.agent_runtime.config import AgentProfileConfig
from reachy_mini.agent_runtime.front_service import FrontService
from reachy_mini.agent_runtime.memory import FrontSessionStore
from reachy_mini.agent_runtime.model_factory import build_front_model
from reachy_mini.agent_runtime.profile_loader import ProfileWorkspace


class FrontAgentRunner:
    """Drive one profile workspace through the front-only text path."""

    def __init__(self, *, profile: ProfileWorkspace, config: AgentProfileConfig):
        """Create the front-only runtime objects."""
        self.profile = profile
        self.config = config
        self.session_store = FrontSessionStore(profile)
        self.front = FrontService(profile, build_front_model(config.front_model))

    async def reply(
        self,
        *,
        thread_id: str,
        user_text: str,
        stream_handler: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Generate one front reply and persist the dialogue."""
        memory = self.session_store.build_memory_view(
            thread_id=thread_id,
            limit=self.config.history_limit,
        )
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
