"""Small rule-based companion intent builder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from reachy_mini.companion.models import CompanionIntent

if TYPE_CHECKING:
    from reachy_mini.affect import AffectState, EmotionSignal

_COMFORT_KEYWORDS = (
    "累",
    "难受",
    "烦",
    "崩溃",
    "压力",
    "焦虑",
    "难过",
    "低落",
    "想哭",
    "tired",
    "sad",
    "stress",
    "overwhelmed",
)
_ENCOURAGE_KEYWORDS = (
    "成功",
    "太好了",
    "开心",
    "搞定",
    "棒",
    "厉害",
    "great",
    "awesome",
    "done",
    "success",
)
_PLAYFUL_KEYWORDS = (
    "嘿嘿",
    "笑死",
    "rua",
    "抱抱",
    "亲亲",
    "无聊",
    "逗我",
    "玩",
)
_FOCUSED_KEYWORDS = (
    "帮我",
    "看一下",
    "看看",
    "检查",
    "分析",
    "搜索",
    "读取",
    "执行",
    "修",
    "改",
    "日志",
    "文件",
    "代码",
    "命令",
    "read",
    "check",
    "search",
    "fix",
    "run",
    "error",
)
_RELATIONSHIP_KEYWORDS = (
    "在吗",
    "陪陪我",
    "陪着我",
    "别走",
    "晚安",
    "早安",
    "谢谢你",
    "想跟你聊聊",
    "stay with me",
    "miss you",
    "thank you",
    "thanks",
)
_DIRECT_HELP_KEYWORDS = (
    "帮我",
    "麻烦你",
    "拜托",
    "可以吗",
    "看看",
    "看一下",
    "处理一下",
    "查一下",
    "please",
)
_SUPPORT_NEED_TO_MODE = {
    "comfort": "comfort",
    "encourage": "encourage",
    "focused": "focused",
    "quiet_company": "quiet_company",
    "celebrate": "encourage",
}
_MODE_DEFAULTS: dict[str, tuple[float, float, float]] = {
    "comfort": (0.92, 0.38, 0.30),
    "encourage": (0.88, 0.62, 0.56),
    "playful": (0.84, 0.68, 0.58),
    "focused": (0.80, 0.58, 0.36),
    "quiet_company": (0.86, 0.36, 0.24),
}


def build_companion_intent(
    *,
    user_text: str,
    kernel_output: str,
    affect_state: "AffectState | None" = None,
    emotion_signal: "EmotionSignal | None" = None,
) -> CompanionIntent:
    """Infer a lightweight companionship strategy for the current turn."""

    user = str(user_text or "").strip().lower()
    kernel = str(kernel_output or "").strip().lower()
    mode = _pick_mode(
        user=user,
        kernel=kernel,
        affect_state=affect_state,
        emotion_signal=emotion_signal,
    )
    warmth, initiative, intensity = _MODE_DEFAULTS[mode]

    if _contains_any(user, _DIRECT_HELP_KEYWORDS):
        initiative += 0.05
    if _contains_any(user, _RELATIONSHIP_KEYWORDS):
        warmth += 0.04
    if user.count("!") + user.count("！") > 0:
        intensity += 0.03

    if affect_state is not None:
        warmth += 0.06 * max(0.0, affect_state.pressure)
        initiative += 0.06 * max(0.0, affect_state.current_pad.dominance)
        intensity += 0.08 * max(0.0, affect_state.current_pad.arousal)
        if affect_state.vitality <= 0.35:
            intensity -= 0.05

    if emotion_signal is not None:
        intensity += 0.08 * max(0.0, float(emotion_signal.intensity or 0.0) - 0.35)
        if str(emotion_signal.support_need or "").strip() == "comfort":
            warmth += 0.03
        if bool(emotion_signal.wants_action):
            initiative += 0.04

    return CompanionIntent(
        mode=mode,
        warmth=_clamp(warmth),
        initiative=_clamp(initiative),
        intensity=_clamp(intensity),
    )


def _pick_mode(
    *,
    user: str,
    kernel: str,
    affect_state: "AffectState | None",
    emotion_signal: "EmotionSignal | None",
) -> str:
    semantic_mode = _pick_mode_from_emotion_signal(emotion_signal)
    if semantic_mode:
        return semantic_mode
    if affect_state is not None and affect_state.pressure >= 0.42 and not _contains_any(user, _FOCUSED_KEYWORDS):
        return "comfort"
    if _contains_any(user, _COMFORT_KEYWORDS):
        return "comfort"
    if _contains_any(user, _ENCOURAGE_KEYWORDS):
        return "encourage"
    if _contains_any(user, _PLAYFUL_KEYWORDS):
        return "playful"
    if _contains_any(user, _FOCUSED_KEYWORDS) or _contains_any(kernel, _FOCUSED_KEYWORDS):
        return "focused"
    if _contains_any(user, _RELATIONSHIP_KEYWORDS) or len(user) <= 14:
        return "quiet_company"
    return "quiet_company"


def _pick_mode_from_emotion_signal(emotion_signal: "EmotionSignal | None") -> str:
    if emotion_signal is None:
        return ""

    support_need = str(emotion_signal.support_need or "").strip()
    mapped_mode = _SUPPORT_NEED_TO_MODE.get(support_need, "")
    if not mapped_mode:
        return ""

    confidence = float(emotion_signal.confidence or 0.0)
    intensity = float(emotion_signal.intensity or 0.0)
    if confidence < 0.45 and intensity < 0.55:
        return ""
    return mapped_mode


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _clamp(value: float, minimum: float = 0.12, maximum: float = 0.98) -> float:
    return max(minimum, min(maximum, value))
