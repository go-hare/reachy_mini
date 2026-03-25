"""Front-plus-kernel runtime runner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from reachy_mini.affect import AffectRuntime, AffectTurnResult, create_affect_runtime
from reachy_mini.agent_core import (
    BrainKernel,
    BrainResponse,
    JsonlMemoryStore,
    TaskType,
    make_id,
)
from reachy_mini.agent_core.memory import MemoryView
from reachy_mini.agent_runtime.config import AgentProfileConfig
from reachy_mini.agent_runtime.model_factory import build_front_model, build_kernel_model
from reachy_mini.agent_runtime.profile_loader import ProfileWorkspace
from reachy_mini.agent_runtime.session_store import FrontSessionStore
from reachy_mini.companion import build_companion_surface
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


def _default_affect_model_path() -> Path:
    """Resolve the bundled Chordia model directory."""
    return Path(__file__).resolve().parents[1] / "mode" / "Chordia"


class FrontAgentRunner:
    """Drive one profile workspace through the text runtime."""

    def __init__(
        self,
        *,
        profile: ProfileWorkspace,
        config: AgentProfileConfig,
        front: FrontService | None = None,
        kernel: BrainKernel | None = None,
        affect_runtime: AffectRuntime | None = None,
    ):
        """Create the runtime objects for one loaded profile."""
        self.profile = profile
        self.config = config
        self.session_store = FrontSessionStore(profile)
        self.front = front or FrontService(profile, build_front_model(config.front_model))
        self.kernel = kernel
        self.affect_runtime = affect_runtime

    @classmethod
    def from_profile(
        cls,
        *,
        profile: ProfileWorkspace,
        config: AgentProfileConfig,
        enable_kernel: bool = True,
        enable_affect: bool = True,
    ) -> "FrontAgentRunner":
        """Build a runner directly from one loaded profile workspace."""
        front = FrontService(profile, build_front_model(config.front_model))
        kernel = None
        affect_runtime = None
        if enable_kernel:
            kernel_model = build_kernel_model(config.kernel_model)
            kernel = BrainKernel(
                agent_id=profile.name,
                model=kernel_model,
                task_router_model=kernel_model,
                memory_store=JsonlMemoryStore(profile.root),
                system_prompt=_build_kernel_system_prompt(profile),
            )
            if enable_affect:
                affect_runtime = create_affect_runtime(profile.root, _default_affect_model_path())
        return cls(
            profile=profile,
            config=config,
            front=front,
            kernel=kernel,
            affect_runtime=affect_runtime,
        )

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

        affect_turn = self._evolve_affect_turn(user_text)
        affect_state = affect_turn.state if affect_turn is not None else None
        emotion_signal = affect_turn.emotion_signal if affect_turn is not None else None

        front_memory = self._build_kernel_memory(thread_id=thread_id, user_text=user_text)
        front_reply = await self.front.reply(
            user_text=user_text,
            memory=front_memory,
            emotion_signal=emotion_signal,
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
                "emotion": emotion_signal.primary_emotion if emotion_signal is not None else "",
                "tags": self._build_front_tags(emotion_signal=emotion_signal),
                "metadata": self._build_front_metadata(
                    source="runner_front_hint",
                    emotion_signal=emotion_signal,
                ),
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

        companion_intent, surface_expression = build_companion_surface(
            user_text=user_text,
            kernel_output=kernel_output,
            affect_state=affect_state,
            emotion_signal=emotion_signal,
        )
        reply = await self.front.present(
            user_text=user_text,
            kernel_output=kernel_output,
            affect_state=affect_state,
            emotion_signal=emotion_signal,
            companion_intent=companion_intent,
            surface_expression=surface_expression,
            style=self.config.front_style,
            stream_handler=stream_handler,
        )
        final_reply = str(reply or "").strip() or kernel_output
        await self.kernel.handle_front_event(
            conversation_id=thread_id,
            turn_id=turn_id,
            front_event={
                "event_type": "dialogue",
                "user_text": user_text,
                "front_reply": final_reply,
                "emotion": surface_expression.expression,
                "tags": self._build_front_tags(
                    emotion_signal=emotion_signal,
                    companion_intent=companion_intent,
                    surface_expression=surface_expression,
                ),
                "metadata": self._build_front_metadata(
                    source="runner_front_delivery",
                    emotion_signal=emotion_signal,
                    affect_state=affect_state,
                    companion_intent=companion_intent,
                    surface_expression=surface_expression,
                    kernel_output=kernel_output,
                ),
            },
        )
        return final_reply

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

    def _evolve_affect_turn(self, user_text: str) -> AffectTurnResult | None:
        """Evolve affect state for one user turn when configured."""
        if self.affect_runtime is None:
            return None
        return self.affect_runtime.evolve(user_text=user_text)

    @staticmethod
    def _build_front_tags(
        *,
        emotion_signal: Any | None = None,
        companion_intent: Any | None = None,
        surface_expression: Any | None = None,
    ) -> list[str]:
        tags = [
            getattr(companion_intent, "mode", ""),
            getattr(surface_expression, "text_style", ""),
            getattr(surface_expression, "presence", ""),
            getattr(surface_expression, "expression", ""),
            getattr(emotion_signal, "primary_emotion", ""),
            getattr(emotion_signal, "support_need", ""),
        ]
        return [str(tag).strip() for tag in tags if str(tag or "").strip()]

    @staticmethod
    def _build_front_metadata(
        *,
        source: str,
        emotion_signal: Any | None = None,
        affect_state: Any | None = None,
        companion_intent: Any | None = None,
        surface_expression: Any | None = None,
        kernel_output: str = "",
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "source": source,
        }
        if kernel_output.strip():
            metadata["kernel_output"] = kernel_output
        if affect_state is not None:
            metadata.update(
                {
                    "affect_pleasure": affect_state.current_pad.pleasure,
                    "affect_arousal": affect_state.current_pad.arousal,
                    "affect_dominance": affect_state.current_pad.dominance,
                    "affect_vitality": affect_state.vitality,
                    "affect_pressure": affect_state.pressure,
                    "affect_updated_at": affect_state.updated_at,
                }
            )
        if emotion_signal is not None:
            metadata.update(
                {
                    "emotion_primary": emotion_signal.primary_emotion,
                    "emotion_intensity": emotion_signal.intensity,
                    "emotion_confidence": emotion_signal.confidence,
                    "emotion_support_need": emotion_signal.support_need,
                    "emotion_wants_action": emotion_signal.wants_action,
                    "emotion_trigger_text": emotion_signal.trigger_text,
                }
            )
        if companion_intent is not None:
            metadata.update(
                {
                    "mode": companion_intent.mode,
                    "warmth": companion_intent.warmth,
                    "initiative": companion_intent.initiative,
                    "intensity": companion_intent.intensity,
                }
            )
        if surface_expression is not None:
            metadata.update(
                {
                    "text_style": surface_expression.text_style,
                    "presence": surface_expression.presence,
                    "expression": surface_expression.expression,
                    "motion_hint": surface_expression.motion_hint,
                    "body_state": surface_expression.body_state,
                    "breathing_hint": surface_expression.breathing_hint,
                    "linger_hint": surface_expression.linger_hint,
                    "speaking_phase": surface_expression.speaking_phase,
                    "settling_phase": surface_expression.settling_phase,
                    "idle_phase": surface_expression.idle_phase,
                }
            )
        return metadata

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
