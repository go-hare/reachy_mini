"""Prompt assembly for the front-only agent runtime."""

from __future__ import annotations

from datetime import datetime

from reachy_mini.agent_runtime.memory import MemoryView

_STYLE_HINTS = {
    "friendly_concise": "语气温和、自然、简洁，像就在桌边陪着说话。",
    "warm_precise": "有温度，但信息仍然很清楚，不拖泥带水。",
    "quiet_company": "更轻、更贴边，少说废话，但一直让人感觉你在。",
}


class FrontPromptBuilder:
    """Build user prompts for the front-only text layer."""

    def build_user_prompt(
        self,
        *,
        user_text: str,
        memory: MemoryView,
        style: str,
    ) -> str:
        """Build the front prompt for one user turn."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sections: list[str] = [
            "## 任务",
            "你现在是 Reachy Mini agent 的 front 层，只负责把这一轮话先自然地接住并回复给用户。",
            "回复要像一个贴在身边的真实伙伴，不要像客服播报。",
            "",
            "## 输出硬约束",
            "只输出用户可见的自然文本。",
            "不要输出括号动作、舞台旁白、系统术语或内部链路名。",
            "不要假装已经看过文件、日志、代码或网页；如果还没看，就明确说会去看。",
            "",
            "## 表达风格",
            _STYLE_HINTS.get(style, _STYLE_HINTS["friendly_concise"]),
            "",
            "## 当前时间",
            now,
        ]

        agent_anchor = str(memory.projections.get("agent_anchor", "") or "").strip()
        soul_anchor = str(memory.projections.get("soul_anchor", "") or "").strip()
        user_anchor = str(memory.projections.get("user_anchor", "") or "").strip()
        tool_anchor = str(memory.projections.get("tool_anchor", "") or "").strip()

        if agent_anchor:
            sections.extend(["", "## Agent 规则", agent_anchor])
        if soul_anchor:
            sections.extend(["", "## 人格锚点", soul_anchor])
        if user_anchor:
            sections.extend(["", "## 用户锚点", user_anchor])
        if tool_anchor:
            sections.extend(["", "## 工具策略", tool_anchor])

        if self.requires_verification(user_text):
            sections.extend(
                [
                    "",
                    "## 回复约束",
                    "这是一个需要先核实内容的请求。",
                    "先接住用户，再表达会去查看、会回来继续说，不要提前编造结果。",
                    "优先一到两句，短一点，但不要冷。",
                ]
            )

        recent_dialogue = list(memory.raw_layer.get("recent_dialogue", []) or [])
        if recent_dialogue:
            lines: list[str] = []
            for row in recent_dialogue[-6:]:
                role = str(row.get("role", "") or "").strip() or "unknown"
                content = str(row.get("content", "") or "").strip()
                if content:
                    lines.append(f"{role}: {content}")
            if lines:
                sections.extend(["", "## 最近对话", "\n".join(lines)])

        sections.extend(["", "## 当前用户输入", user_text.strip()])
        return "\n".join(part for part in sections if part is not None)

    @staticmethod
    def requires_verification(user_text: str) -> bool:
        """Detect requests that should first acknowledge then verify."""
        text = str(user_text or "").strip().lower()
        if not text:
            return False
        keywords = (
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
        )
        return any(keyword in text for keyword in keywords)
