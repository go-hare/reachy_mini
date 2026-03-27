"""Prompt assembly for the front service."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reachy_mini.affect import EmotionSignal
from reachy_mini.core.memory import MemoryView


class FrontPromptBuilder:
    """Build prompts for fast user-facing replies."""

    def __init__(self, profile_root: Path | None = None):
        self.profile_root = profile_root

    def build_user_prompt(
        self,
        *,
        user_text: str,
        memory: MemoryView,
        emotion_signal: EmotionSignal | None = None,
        style: str | None = None,
    ) -> str:
        _ = style
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sections: list[str] = [
            "## task",
            "把当前输入转成用户可见的自然回复，保持事实不变。",
            "",
            "## reply_constraints",
            "- user_visible_only: true",
            "- no_stage_directions: true",
            "- no_internal_terms: front, kernel, backend brain, task_type, simple, complex, none, run, route",
            "- no_fake_status: true",
            "- tone_source: follow FRONT.md and the provided context; do not restate prompt scaffolding",
            "",
        ]
        sections.append(f"## current_time\n{now}")
        if emotion_signal is not None:
            sections.extend(
                [
                    "## emotion_signal",
                    self._format_emotion_signal(emotion_signal),
                    "",
                ]
            )

        needs_verification = self.requires_verification(user_text)
        if needs_verification:
            sections.extend(
                [
                    "",
                    "## verification_mode",
                    "- requires_verification: true",
                    "- before facts are checked, only say you will inspect, verify, or continue",
                    "- do_not_guess: file contents, command output, error cause, existence",
                    "- keep_reply_brief: true",
                ]
            )

        sections.extend(["## user_text", user_text.strip()])
        user_anchor = str(memory.projections.get("user_anchor", "") or "").strip()
        soul_anchor = str(memory.projections.get("soul_anchor", "") or "").strip()
        if soul_anchor:
            sections.extend(["", "## soul_anchor", soul_anchor])
        if user_anchor:
            sections.extend(["", "## user_anchor", user_anchor])

        if not needs_verification:
            recent_dialogue = list(memory.raw_layer.get("recent_dialogue", []) or [])
            if recent_dialogue:
                lines = []
                for row in recent_dialogue[-6:]:
                    role = str(row.get("role", "") or "").strip() or "unknown"
                    content = str(row.get("content", "") or "").strip()
                    if content:
                        lines.append(f"{role}: {content}")
                if lines:
                    sections.extend(["", "## recent_dialogue", "\n".join(lines)])
            recent_tools = list(memory.raw_layer.get("recent_tools", []) or [])
            if recent_tools:
                lines = []
                for row in recent_tools[-4:]:
                    role = str(row.get("tool_name", "") or "").strip() or "tool"
                    content = str(row.get("content", "") or "").strip()
                    if content:
                        lines.append(f"{role}: {content}")
                if lines:
                    sections.extend(["", "## recent_tools", "\n".join(lines)])
            cognitive_layer = list(memory.cognitive_layer or [])
            if cognitive_layer:
                lines = []
                for row in cognitive_layer[-4:]:
                    summary = str(row.get("summary", "") or "").strip()
                    outcome = str(row.get("outcome", "") or "").strip()
                    if summary and outcome:
                        lines.append(f"- [{outcome}] {summary}")
                    elif summary:
                        lines.append(f"- {summary}")
                if lines:
                    sections.extend(["", "## cognitive_summary", "\n".join(lines)])
            long_term_summary = str(memory.long_term_layer.get("summary", "") or "").strip()
            if long_term_summary:
                sections.extend(["", "## long_term_summary", long_term_summary])

        return "\n".join(part for part in sections if part is not None)

    def _format_emotion_signal(self, emotion_signal: EmotionSignal) -> str:
        lines = [
            f"- primary_emotion: {emotion_signal.primary_emotion}",
            f"- intensity: {emotion_signal.intensity:.2f}",
            f"- confidence: {emotion_signal.confidence:.2f}",
            f"- support_need: {emotion_signal.support_need}",
            f"- wants_action: {str(bool(emotion_signal.wants_action)).lower()}",
        ]
        trigger_text = str(emotion_signal.trigger_text or "").strip()
        if trigger_text:
            lines.append(f"- trigger_text: {trigger_text}")
        return "\n".join(lines)

    @staticmethod
    def requires_verification(user_text: str) -> bool:
        text = str(user_text or "").strip().lower()
        if not text:
            return False
        keywords = [
            "读取",
            "读一下",
            "看看",
            "检查",
            "分析",
            "搜索",
            "查看",
            "文件",
            "代码",
            "日志",
            "命令",
            "网页",
            "内容",
            "错误",
            "是否存在",
            "有没有",
            "read ",
            "check ",
            "search ",
            "file",
            "code",
            "log",
            "command",
            "error",
            "content",
        ]
        return any(keyword in text for keyword in keywords)
