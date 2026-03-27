"""User-facing front service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

from reachy_mini.affect import AffectState, EmotionSignal
from reachy_mini.core.memory import MemoryView
from reachy_mini.companion import (
    CompanionIntent,
    SurfaceExpression,
    build_idle_surface_state,
    build_listening_surface_state,
    build_listening_wait_surface_state,
)
from reachy_mini.front.events import FrontDecision, FrontSignal, FrontSignalResult, FrontToolCall
from reachy_mini.front.prompt import FrontPromptBuilder
from reachy_mini.utils.llm_utils import extract_message_text

if TYPE_CHECKING:
    from reachy_mini.runtime.profile_loader import ProfileBundle

_SURFACE_PATCH_METADATA_KEYS = (
    "recommended_hold_ms",
    "idle_seconds",
    "chunk_bytes",
)


class FrontService:
    """Fast conversational layer that talks to the user first."""

    def __init__(
        self,
        profile: "ProfileBundle",
        model: Any,
        *,
        tools: list[Any] | None = None,
    ):
        self.profile = profile
        self.profile_root = profile.root
        self.model = model
        self.tools = list(tools or [])
        self.prompts = FrontPromptBuilder(self.profile_root)
        self._signal_history: dict[str, list[FrontSignal]] = {}
        self._latest_signal_result: dict[str, FrontDecision] = {}

    @property
    def tool_names(self) -> list[str]:
        return [str(getattr(tool, "name", "") or "").strip() for tool in self.tools]

    def get_tool(self, name: str) -> Any | None:
        resolved_name = str(name or "").strip()
        if not resolved_name:
            return None
        for tool in self.tools:
            if str(getattr(tool, "name", "") or "").strip() == resolved_name:
                return tool
        return None

    def get_signal_history(self, thread_id: str) -> list[FrontSignal]:
        return list(self._signal_history.get(str(thread_id or ""), []))

    def get_latest_signal_result(self, thread_id: str) -> FrontDecision | None:
        return self._latest_signal_result.get(str(thread_id or ""))

    def get_latest_front_decision(self, thread_id: str) -> FrontDecision | None:
        return self.get_latest_signal_result(thread_id)

    async def handle_signal(self, signal: FrontSignal) -> FrontSignalResult:
        """Consume one expressive lifecycle signal from the runtime."""
        thread_id = str(signal.thread_id or "").strip() or "app:main"
        normalized_signal = FrontSignal(
            name=str(signal.name or "").strip(),
            thread_id=thread_id,
            turn_id=str(signal.turn_id or "").strip(),
            user_text=str(signal.user_text or ""),
            metadata=dict(signal.metadata or {}),
        )
        history = self._signal_history.setdefault(thread_id, [])
        history.append(normalized_signal)
        if len(history) > 24:
            del history[:-24]

        result = self.decide_front_action(normalized_signal)
        self._latest_signal_result[thread_id] = result
        return result

    def decide_front_action(self, signal: FrontSignal) -> FrontDecision:
        """Build one lightweight expressive decision from a front signal."""
        lifecycle_state = self._derive_lifecycle_state(signal.name)
        tool_calls, debug_reason = self._derive_tool_calls(
            signal=signal,
            lifecycle_state=lifecycle_state,
        )
        return FrontDecision(
            signal_name=signal.name,
            thread_id=signal.thread_id,
            turn_id=signal.turn_id,
            reply_text="",
            lifecycle_state=lifecycle_state,
            surface_patch=self._derive_surface_patch(
                signal=signal,
                lifecycle_state=lifecycle_state,
            ),
            tool_calls=tool_calls,
            debug_reason=debug_reason,
        )

    async def reply(
        self,
        *,
        user_text: str,
        memory: MemoryView,
        emotion_signal: EmotionSignal | None = None,
        stream_handler: Callable[[str], Awaitable[None]] | None = None,
        style: str | None = None,
    ) -> str:
        _ = style
        system_text = self._front_system_text()
        user_prompt = self.prompts.build_user_prompt(
            user_text=user_text,
            memory=memory,
            emotion_signal=emotion_signal,
            style=style,
        )
        messages = [SystemMessage(content=system_text), HumanMessage(content=user_prompt)]
        return await self.run(messages, stream_handler)

    async def present(
        self,
        *,
        user_text: str,
        kernel_output: str,
        affect_state: AffectState | None = None,
        emotion_signal: EmotionSignal | None = None,
        companion_intent: CompanionIntent | None = None,
        surface_expression: SurfaceExpression | None = None,
        stream_handler: Callable[[str], Awaitable[None]] | None = None,
        style: str | None = None,
    ) -> str:
        _ = style
        if not str(kernel_output or "").strip():
            return ""

        messages = [
            SystemMessage(content=self._front_system_text()),
            HumanMessage(
                content=self._build_presentation_prompt(
                    user_text=user_text,
                    kernel_output=kernel_output,
                    affect_state=affect_state,
                    emotion_signal=emotion_signal,
                    companion_intent=companion_intent,
                    surface_expression=surface_expression,
                )
            ),
        ]
        return await self.run(messages, stream_handler)

    async def run(
        self,
        messages: list[SystemMessage | HumanMessage],
        stream_handler: Callable[[str], Awaitable[None]] | None,
    ) -> str:
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
        return self.profile.front_md.strip()

    @staticmethod
    def _derive_lifecycle_state(signal_name: str) -> str:
        mapping = {
            "turn_started": "listening",
            "listening_entered": "listening",
            "user_speech_started": "listening",
            "user_speech_stopped": "listening_wait",
            "kernel_output_ready": "replying",
            "assistant_audio_started": "replying",
            "assistant_audio_delta": "replying",
            "assistant_audio_finished": "settling",
            "settling_entered": "settling",
            "turn_settling": "settling",
            "idle_tick": "idle",
            "idle_entered": "idle",
            "vision_attention_updated": "attending",
        }
        return mapping.get(str(signal_name or "").strip(), "observing")

    @staticmethod
    def _derive_surface_patch(
        *,
        signal: FrontSignal,
        lifecycle_state: str,
    ) -> dict[str, Any]:
        patch = FrontService._build_default_surface_patch(
            thread_id=signal.thread_id,
            lifecycle_state=lifecycle_state,
        )
        patch["phase"] = lifecycle_state
        patch["source_signal"] = signal.name
        return FrontService._merge_surface_metadata(
            patch=patch,
            metadata=dict(signal.metadata or {}),
        )

    @staticmethod
    def _build_default_surface_patch(
        *,
        thread_id: str,
        lifecycle_state: str,
    ) -> dict[str, Any]:
        if lifecycle_state == "listening":
            return FrontService._surface_patch_from_state(
                build_listening_surface_state(thread_id=thread_id)
            )

        if lifecycle_state == "listening_wait":
            return FrontService._surface_patch_from_state(
                build_listening_wait_surface_state(thread_id=thread_id)
            )

        if lifecycle_state == "idle":
            return FrontService._surface_patch_from_state(
                build_idle_surface_state(thread_id=thread_id)
            )

        if lifecycle_state == "replying":
            return {
                "phase": "replying",
                "recommended_hold_ms": 0,
            }

        if lifecycle_state == "settling":
            return {
                "phase": "settling",
                "recommended_hold_ms": 900,
            }

        if lifecycle_state == "attending":
            return {
                "phase": "attending",
                "recommended_hold_ms": 0,
            }

        return {
            "phase": "observing",
            "recommended_hold_ms": 0,
        }

    @staticmethod
    def _surface_patch_from_state(state: dict[str, Any]) -> dict[str, Any]:
        patch = dict(state)
        patch.pop("thread_id", None)
        return patch

    @staticmethod
    def _merge_surface_metadata(
        *,
        patch: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        for key in _SURFACE_PATCH_METADATA_KEYS:
            if key not in metadata:
                continue
            value = metadata[key]
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            patch[key] = value
        if "kernel_output" in metadata:
            patch["has_kernel_output"] = bool(str(metadata["kernel_output"]).strip())
        return patch

    def _derive_tool_calls(
        self,
        *,
        signal: FrontSignal,
        lifecycle_state: str,
    ) -> tuple[list[FrontToolCall], str]:
        tool_names = set(self.tool_names)
        metadata = dict(signal.metadata or {})

        if signal.name == "user_speech_started":
            stop_calls = self._build_stop_expression_calls(
                tool_names=tool_names,
                reason="yield the floor to the user and clear leftover expression",
            )
            if stop_calls:
                tool_list = ", ".join(call.tool_name for call in stop_calls)
                return stop_calls, f"front cleared expressive loop(s) for user speech: {tool_list}"
            if "do_nothing" in tool_names:
                return (
                    [
                        FrontToolCall(
                            tool_name="do_nothing",
                            arguments={"reason": "User is speaking; hold a quiet listening posture."},
                            reason="yield the floor to user speech",
                        )
                    ],
                    "front selected do_nothing to yield for user speech",
                )

        if signal.name == "assistant_audio_started":
            stop_calls = self._build_stop_expression_calls(
                tool_names=tool_names,
                reason="hand expressive control back to reply audio",
            )
            if stop_calls:
                tool_list = ", ".join(call.tool_name for call in stop_calls)
                return stop_calls, f"front cleared expressive loop(s) for assistant audio: {tool_list}"

        if signal.name == "user_speech_stopped" and "do_nothing" in tool_names:
            return (
                [
                    FrontToolCall(
                        tool_name="do_nothing",
                        arguments={"reason": "User finished speaking; hold a close listening-wait posture."},
                        reason="listening_wait hold",
                    )
                ],
                "front selected do_nothing for listening_wait handoff",
            )

        if signal.name == "settling_entered" and "do_nothing" in tool_names:
            return (
                [
                    FrontToolCall(
                        tool_name="do_nothing",
                        arguments={"reason": "Reply finished; hold a short settling posture before idling."},
                        reason="settling hold",
                    )
                ],
                "front selected do_nothing for settling_entered",
            )

        if signal.name == "idle_tick":
            if "move_head" in tool_names:
                direction = self._resolve_idle_look_direction(signal.thread_id)
                return (
                    [
                        FrontToolCall(
                            tool_name="move_head",
                            arguments={"direction": direction},
                            reason="idle look-around",
                        )
                    ],
                    f"front selected move_head:{direction} for idle_tick",
                )
            if "do_nothing" in tool_names:
                return (
                    [
                        FrontToolCall(
                            tool_name="do_nothing",
                            arguments={"reason": "Hold a calm idle posture while waiting."},
                            reason="idle hold",
                        )
                    ],
                    "front selected do_nothing fallback for idle_tick",
                )

        if lifecycle_state == "idle" and "do_nothing" in tool_names:
            return (
                [
                    FrontToolCall(
                        tool_name="do_nothing",
                        arguments={"reason": "Hold a calm idle posture while waiting."},
                        reason="idle hold",
                    )
                ],
                f"front selected do_nothing for {signal.name}",
            )

        if signal.name == "vision_attention_updated":
            direction = str(metadata.get("direction", "") or "").strip().lower()
            if direction in {"left", "right", "up", "down", "front"} and "move_head" in tool_names:
                return (
                    [
                        FrontToolCall(
                            tool_name="move_head",
                            arguments={"direction": direction},
                            reason="align gaze with vision attention update",
                        )
                    ],
                    f"front selected move_head:{direction} for vision attention",
                )
            if "tracking_enabled" in metadata and "head_tracking" in tool_names:
                return (
                    [
                        FrontToolCall(
                            tool_name="head_tracking",
                            arguments={"start": bool(metadata["tracking_enabled"])},
                            reason="toggle head tracking from vision attention update",
                        )
                    ],
                    "front toggled head_tracking from vision attention update",
                )

        return [], f"front accepted signal {signal.name} without explicit tool call"

    @staticmethod
    def _build_stop_expression_calls(
        *,
        tool_names: set[str],
        reason: str,
    ) -> list[FrontToolCall]:
        calls: list[FrontToolCall] = []
        if "stop_emotion" in tool_names:
            calls.append(
                FrontToolCall(
                    tool_name="stop_emotion",
                    reason=reason,
                )
            )
        if "stop_dance" in tool_names:
            calls.append(
                FrontToolCall(
                    tool_name="stop_dance",
                    reason=reason,
                )
            )
        return calls

    def _resolve_idle_look_direction(self, thread_id: str) -> str:
        history = self.get_signal_history(thread_id)
        idle_tick_count = sum(1 for item in history if item.name == "idle_tick")
        directions = ("left", "right", "front")
        return directions[max(idle_tick_count - 1, 0) % len(directions)]

    def _build_presentation_prompt(
        self,
        *,
        user_text: str,
        kernel_output: str,
        affect_state: AffectState | None = None,
        emotion_signal: EmotionSignal | None = None,
        companion_intent: CompanionIntent | None = None,
        surface_expression: SurfaceExpression | None = None,
    ) -> str:
        sections = [
            "## 任务",
            "你现在只负责把后台主脑的原始结果转成给用户看的自然回复。",
            "保留事实、路径、命令、错误、步骤和结论，不要改动真实含义。",
            "允许带一点陪伴感，但不要扩写事实，也不要捏造系统状态。",
            "",
            "## 回复要求",
            "1. 先轻轻接住用户，再给真正的信息、步骤、判断或结论。",
            "2. 主体仍然以后台结果为准，不要让陪伴表达盖过事实。",
            "3. 用自然口语直接对用户说，不要写提示词解释或内部分析。",
            "",
            "## 输出硬约束",
            "1. 只输出用户可见的自然口语，不要写动作旁白或舞台指令（如“（轻轻一笑）”“[点头]”）。",
            "2. 不要出现内部术语：前台、内核、后台主脑、task_type、simple、complex、none、run、route、event_id。",
            "3. 如果后台原始结果里夹带路由/状态字段，只提炼用户需要的信息，不复述内部字段名。",
            "4. 不要给出系统自评（如“我这边很稳”“链路正常”），直接回应用户和事实结果。",
            "",
            "## 用户原始输入",
            str(user_text or "").strip(),
        ]
        if affect_state is not None:
            sections.extend(
                [
                    "",
                    "## affect_state",
                    self._format_affect_state(affect_state),
                ]
            )
        if emotion_signal is not None:
            sections.extend(
                [
                    "",
                    "## emotion_signal",
                    self._format_emotion_signal(emotion_signal),
                ]
            )
        if companion_intent is not None:
            sections.extend(
                [
                    "",
                    "## companion_intent",
                    self._format_companion_intent(companion_intent),
                ]
            )
        if surface_expression is not None:
            sections.extend(
                [
                    "",
                    "## surface_expression",
                    self._format_surface_expression(surface_expression),
                ]
            )
        sections.extend(
            [
                "",
                "## 输出重点",
                self._build_information_balance_hint(kernel_output),
                "",
                "## 后台主脑原始结果",
                str(kernel_output or "").strip(),
            ]
        )
        return "\n".join(sections).strip()

    def _format_affect_state(self, affect_state: AffectState) -> str:
        return "\n".join(
            [
                f"- pleasure: {affect_state.current_pad.pleasure:.2f}",
                f"- arousal: {affect_state.current_pad.arousal:.2f}",
                f"- dominance: {affect_state.current_pad.dominance:.2f}",
                f"- vitality: {affect_state.vitality:.2f}",
                f"- pressure: {affect_state.pressure:.2f}",
            ]
        )

    def _format_emotion_signal(self, emotion_signal: EmotionSignal) -> str:
        trigger_text = str(emotion_signal.trigger_text or "").strip()
        lines = [
            f"- primary_emotion: {emotion_signal.primary_emotion}",
            f"- intensity: {emotion_signal.intensity:.2f}",
            f"- confidence: {emotion_signal.confidence:.2f}",
            f"- support_need: {emotion_signal.support_need}",
            f"- wants_action: {str(bool(emotion_signal.wants_action)).lower()}",
        ]
        if trigger_text:
            lines.append(f"- trigger_text: {trigger_text}")
        return "\n".join(lines)

    def _format_companion_intent(self, intent: CompanionIntent) -> str:
        return "\n".join(
            [
                f"- mode: {intent.mode}",
                f"- warmth: {intent.warmth:.2f}",
                f"- initiative: {intent.initiative:.2f}",
                f"- intensity: {intent.intensity:.2f}",
            ]
        )

    def _format_surface_expression(self, expression: SurfaceExpression) -> str:
        return "\n".join(
            [
                f"- text_style: {expression.text_style}",
                f"- expression: {expression.expression}",
            ]
        )

    def _build_information_balance_hint(self, kernel_output: str) -> str:
        text = str(kernel_output or "").strip()
        if len(text) >= 180 or text.count("\n") >= 4:
            return "后台信息较多，接住用户一句就够，正文以后台结果为主。"
        return "可以带一句轻陪伴，但不要盖过后台结果里的有效信息。"
