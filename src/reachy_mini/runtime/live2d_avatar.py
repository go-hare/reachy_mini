"""Live2D avatar capability loading for resident runtime profiles."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Live2DNativeAction:
    """One native Live2D motion or expression with model-derived semantics."""

    name: str
    file_name: str
    aliases: tuple[str, ...] = ()
    parameter_ids: tuple[str, ...] = ()
    parameter_labels: tuple[str, ...] = ()
    duration_s: float | None = None


@dataclass(frozen=True)
class Live2DCapabilities:
    """Native Live2D capabilities exposed to the Brain action registry."""

    model_name: str
    root_url: str
    vtube_file: str
    model_file: str
    idle_motion: str
    motions: tuple[str, ...]
    expressions: tuple[str, ...]
    motion_details: tuple[Live2DNativeAction, ...] = ()
    expression_details: tuple[Live2DNativeAction, ...] = ()


def load_live2d_capabilities(profile_root: Path | str) -> Live2DCapabilities | None:
    """Load Live2D capabilities from an app profile's static avatar config."""

    root = _normalize_profile_root(Path(profile_root))
    static_dir = _resolve_static_dir(root)
    if static_dir is None:
        return None

    config_path = static_dir / "avatar.config.json"
    if not config_path.is_file():
        return None

    avatar_config = _read_json(config_path)
    if str(avatar_config.get("mode", "")).strip().lower() != "live2d":
        return None

    live2d = avatar_config.get("live2d") or {}
    if not isinstance(live2d, dict):
        raise ValueError("avatar.config.json live2d section must be an object.")

    root_url = str(live2d.get("root") or "").strip().rstrip("/")
    vtube_file = str(live2d.get("vtube") or "").strip()
    if not root_url or not vtube_file:
        raise ValueError("Live2D mode requires live2d.root and live2d.vtube.")

    model_root = _resolve_static_url(static_dir, root_url)
    vtube_path = model_root / vtube_file
    vtube = _read_json(vtube_path)
    file_refs = vtube.get("FileReferences") or {}
    if not isinstance(file_refs, dict):
        raise ValueError(f"{vtube_path} FileReferences must be an object.")

    model_file = str(file_refs.get("Model") or "").strip()
    idle_file = str(file_refs.get("IdleAnimation") or "").strip()
    if not model_file:
        raise ValueError(f"{vtube_path} does not reference a model3 file.")

    parameter_labels = _read_parameter_labels(model_root, model_file)

    motions: list[str] = []
    motion_details: list[Live2DNativeAction] = []
    if idle_file:
        idle_detail = _build_native_action(
            model_root,
            idle_file,
            kind="motion",
            parameter_labels=parameter_labels,
        )
        motions.append(idle_detail.name)
        motion_details.append(idle_detail)

    expressions: list[str] = []
    seen_motions = set(motions)
    seen_expressions: set[str] = set()
    expression_details: list[Live2DNativeAction] = []
    hotkeys = vtube.get("Hotkeys") or []
    if not isinstance(hotkeys, list):
        raise ValueError(f"{vtube_path} Hotkeys must be an array.")

    for hotkey in hotkeys:
        if not isinstance(hotkey, dict):
            continue
        action = str(hotkey.get("Action") or "").strip()
        file_name = str(hotkey.get("File") or "").strip()
        if not file_name:
            continue
        name = _strip_live2d_extension(file_name)
        if action == "TriggerAnimation" and name not in seen_motions:
            detail = _build_native_action(
                model_root,
                file_name,
                kind="motion",
                parameter_labels=parameter_labels,
            )
            motions.append(detail.name)
            motion_details.append(detail)
            seen_motions.add(name)
        elif action == "ToggleExpression" and name not in seen_expressions:
            detail = _build_native_action(
                model_root,
                file_name,
                kind="expression",
                parameter_labels=parameter_labels,
            )
            expressions.append(detail.name)
            expression_details.append(detail)
            seen_expressions.add(name)

    return Live2DCapabilities(
        model_name=model_root.name,
        root_url=root_url,
        vtube_file=vtube_file,
        model_file=model_file,
        idle_motion=_strip_live2d_extension(idle_file),
        motions=tuple(motions),
        expressions=tuple(expressions),
        motion_details=tuple(motion_details),
        expression_details=tuple(expression_details),
    )


def live2d_system_prompt(capabilities: Live2DCapabilities) -> str:
    """Build the system prompt section that makes Live2D capabilities explicit."""

    motions = _format_prompt_actions(
        capabilities.motion_details,
        capabilities.motions,
        kind="motion",
        fallback_suffix=".motion3.json",
    )
    expressions = _format_prompt_actions(
        capabilities.expression_details,
        capabilities.expressions,
        kind="expression",
        fallback_suffix=".exp3.json",
    )
    return (
        "Current embodiment mode: Live2D avatar.\n"
        "This mode is a separate native Live2D body, not the Reachy robot body and "
        "not the sprite desktop-pet template.\n"
        "The action registry has generated one concrete tool for each native "
        "Live2D .motion3.json and .exp3.json capability loaded from the model JSON. "
        "Call those concrete Live2D tools directly when the user asks for movement "
        "or expression; do not invent string names.\n"
        "If the user asks you to perform, show, change, move, wave, blink, make an "
        "expression, or otherwise animate the avatar, you MUST call the matching "
        "Live2D tool before replying. A natural-language promise such as 'I will "
        "wave' is not enough.\n"
        "After a Live2D tool call succeeds, finish the turn with one short sentence "
        "confirming the exact native action name you used.\n"
        "Every listed Live2D action below is already implemented and available. "
        "Do not tell the user that a listed action needs a developer to add it.\n"
        "Do not call Reachy recorded-move, antenna, head, body, or sprite-pose actions "
        "while this mode is active.\n"
        "Native Live2D motion tools and meanings:\n"
        f"{motions}\n"
        "Native Live2D expression tools and meanings:\n"
        f"{expressions}\n"
        "Critical mapping: when the user asks for 挥手, 举手, 招手, 抬手, wave, "
        "or raise hand, call live2d_motion_huishou."
    )


def live2d_tool_name(kind: str, native_name: str) -> str:
    """Return the deterministic tool name for a native Live2D action."""

    normalized_kind = str(kind).strip().lower()
    if normalized_kind not in {"motion", "expression"}:
        raise ValueError(f"Unsupported Live2D tool kind: {kind!r}")
    return f"live2d_{normalized_kind}_{_tool_slug(native_name)}"


def _normalize_profile_root(path: Path) -> Path:
    if path.is_file():
        return path.parent
    return path


def _resolve_static_dir(profile_root: Path) -> Path | None:
    app_root = profile_root.parent
    candidates = [
        app_root / app_root.name / "static",
        app_root / "static",
        profile_root / "static",
    ]
    for candidate in candidates:
        if (candidate / "avatar.config.json").is_file():
            return candidate
    return None


def _resolve_static_url(static_dir: Path, root_url: str) -> Path:
    if root_url.startswith("/static/"):
        return static_dir / root_url.removeprefix("/static/")
    path = Path(root_url).expanduser()
    if path.is_absolute():
        return path
    return static_dir / path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"Live2D config file is missing: {path}") from None
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return value


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return value


def _read_parameter_labels(model_root: Path, model_file: str) -> dict[str, str]:
    model = _read_optional_json(model_root / model_file)
    if model is None:
        return {}
    file_refs = model.get("FileReferences") or {}
    if not isinstance(file_refs, dict):
        return {}
    display_info = str(file_refs.get("DisplayInfo") or "").strip()
    if not display_info:
        return {}
    cdi = _read_optional_json(model_root / display_info)
    if cdi is None:
        return {}
    labels: dict[str, str] = {}
    parameters = cdi.get("Parameters") or []
    if not isinstance(parameters, list):
        return labels
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        parameter_id = str(parameter.get("Id") or "").strip()
        label = _clean_label(parameter.get("Name"))
        if parameter_id and label:
            labels[parameter_id] = label
    return labels


def _build_native_action(
    model_root: Path,
    file_name: str,
    *,
    kind: str,
    parameter_labels: dict[str, str],
) -> Live2DNativeAction:
    name = _strip_live2d_extension(file_name)
    parameter_ids: tuple[str, ...]
    duration_s: float | None
    if kind == "motion":
        parameter_ids, duration_s = _read_motion_details(model_root / file_name)
    else:
        parameter_ids = _read_expression_parameter_ids(model_root / file_name)
        duration_s = None
    labels = _labels_for_parameters(parameter_ids, parameter_labels)
    aliases = _aliases_for_native_action(name, labels, kind=kind)
    return Live2DNativeAction(
        name=name,
        file_name=file_name,
        aliases=aliases,
        parameter_ids=parameter_ids,
        parameter_labels=labels,
        duration_s=duration_s,
    )


def _read_motion_details(path: Path) -> tuple[tuple[str, ...], float | None]:
    data = _read_optional_json(path)
    if data is None:
        return (), None
    parameter_ids: list[str] = []
    curves = data.get("Curves") or []
    if isinstance(curves, list):
        for curve in curves:
            if not isinstance(curve, dict) or curve.get("Target") != "Parameter":
                continue
            parameter_id = str(curve.get("Id") or "").strip()
            if parameter_id:
                parameter_ids.append(parameter_id)
    duration = data.get("Meta", {}).get("Duration")
    duration_s = float(duration) if isinstance(duration, (int, float)) else None
    return _dedupe(parameter_ids), duration_s


def _read_expression_parameter_ids(path: Path) -> tuple[str, ...]:
    data = _read_optional_json(path)
    if data is None:
        return ()
    parameter_ids: list[str] = []
    parameters = data.get("Parameters") or []
    if isinstance(parameters, list):
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            parameter_id = str(parameter.get("Id") or "").strip()
            if parameter_id:
                parameter_ids.append(parameter_id)
    return _dedupe(parameter_ids)


def _labels_for_parameters(
    parameter_ids: tuple[str, ...],
    parameter_labels: dict[str, str],
) -> tuple[str, ...]:
    return _dedupe(
        label for parameter_id in parameter_ids if (label := parameter_labels.get(parameter_id))
    )


def _aliases_for_native_action(
    name: str,
    parameter_labels: tuple[str, ...],
    *,
    kind: str,
) -> tuple[str, ...]:
    aliases: list[str] = [name, *parameter_labels]
    comparable = {item.lower() for item in aliases}

    if kind == "motion":
        if "huishou" in comparable or "挥手" in aliases:
            aliases.extend(("挥手", "举手", "招手", "抬手", "wave", "raise hand", "hand up"))
        if "meiyan" in comparable or "爱心轨迹" in aliases:
            aliases.extend(("媚眼", "抛媚眼", "眨眼", "wink", "heart wink"))
        if "daiji" in comparable:
            aliases.extend(("待机", "空闲", "idle", "idle motion"))
        return _dedupe(aliases)

    expression_aliases = {
        "惊讶": ("惊讶", "吃惊", "震惊", "surprised", "surprise"),
        "脸红": ("脸红", "害羞", "羞涩", "blush", "shy"),
        "脸黑": ("脸黑", "黑脸", "阴沉", "dark face"),
        "疑惑": ("疑惑", "困惑", "问号", "confused"),
        "生气": ("生气", "愤怒", "怒", "angry"),
        "爱心眼": ("爱心眼", "爱心", "heart eyes"),
        "星星眼": ("星星眼", "星星", "star eyes"),
        "金钱眼": ("金钱眼", "金钱", "money eyes"),
        "白眼": ("白眼", "翻白眼", "roll eyes"),
        "流泪": ("流泪", "哭", "哭泣", "tears", "cry"),
        "披发": ("披发", "散发", "后发"),
        "王冠": ("王冠", "皇冠", "crown"),
        "翅膀": ("翅膀", "wing", "wings"),
        "舌头": ("舌头", "吐舌", "tongue"),
        "猫耳": ("猫耳", "耳朵", "cat ears"),
        "手柄": ("手柄", "游戏手柄", "controller"),
        "直播套装": ("直播套装", "直播", "家具", "streaming set"),
        "马尾": ("马尾", "ponytail"),
        "歪嘴": ("歪嘴", "坏笑", "smirk"),
    }
    for trigger, values in expression_aliases.items():
        if trigger in name or trigger in aliases:
            aliases.extend(values)
    return _dedupe(aliases)


def _format_prompt_actions(
    details: tuple[Live2DNativeAction, ...],
    names: tuple[str, ...],
    *,
    kind: str,
    fallback_suffix: str,
) -> str:
    detail_by_name = {detail.name: detail for detail in details}
    lines: list[str] = []
    for name in names:
        detail = detail_by_name.get(
            name,
            Live2DNativeAction(name=name, file_name=f"{name}{fallback_suffix}"),
        )
        tool_name = live2d_tool_name(kind, detail.name)
        aliases = ", ".join(detail.aliases) if detail.aliases else detail.name
        parameters = _format_parameters(detail)
        line = (
            f"- {tool_name} / mcp__reachy_actions__{tool_name}: "
            f"native={detail.name}, file={detail.file_name}, aliases={aliases}"
        )
        if parameters:
            line = f"{line}, parameters={parameters}"
        lines.append(line)
    return "\n".join(lines) if lines else "- (none)"


def _format_parameters(detail: Live2DNativeAction) -> str:
    if not detail.parameter_ids:
        return ""
    values: list[str] = []
    labels_by_index = list(detail.parameter_labels)
    for index, parameter_id in enumerate(detail.parameter_ids):
        label = ""
        if len(labels_by_index) == 1:
            label = labels_by_index[0]
        elif index < len(labels_by_index):
            label = labels_by_index[index]
        values.append(f"{parameter_id}({label})" if label else parameter_id)
    return ", ".join(values)


def _clean_label(value: Any) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split()).strip()


def _dedupe(values: Any) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _clean_label(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _strip_live2d_extension(file_name: str) -> str:
    value = str(file_name or "").strip()
    for suffix in (".motion3.json", ".exp3.json"):
        if value.lower().endswith(suffix):
            return value[: -len(suffix)]
    return value


def _tool_slug(value: str) -> str:
    readable = _readable_slug(value)
    if readable:
        return readable
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
    if slug:
        return slug
    codepoints = "_".join(f"u{ord(char):x}" for char in value)
    return codepoints or "action"


def _readable_slug(value: str) -> str:
    tokens: list[str] = []
    ascii_buffer: list[str] = []

    def flush_ascii_buffer() -> None:
        if ascii_buffer:
            tokens.append("".join(ascii_buffer))
            ascii_buffer.clear()

    for char in value:
        if char.isascii() and char.isalnum():
            ascii_buffer.append(char.lower())
            continue
        token = _LIVE2D_SLUG_TOKENS.get(char)
        if token:
            flush_ascii_buffer()
            tokens.append(token)
            continue
        if char.isspace() or char in {"-", "_", "~", "·"}:
            flush_ascii_buffer()
            tokens.append("_")
            continue
        return ""
    flush_ascii_buffer()
    slug = "_".join(token for token in tokens if token != "_")
    return re.sub(r"_+", "_", slug).strip("_")


_LIVE2D_SLUG_TOKENS = {
    "←": "left",
    "→": "right",
    "歪": "wai",
    "嘴": "zui",
    "惊": "jing",
    "讶": "ya",
    "脸": "lian",
    "红": "hong",
    "黑": "hei",
    "疑": "yi",
    "惑": "huo",
    "生": "sheng",
    "气": "qi",
    "爱": "ai",
    "心": "xin",
    "眼": "yan",
    "星": "xing",
    "金": "jin",
    "钱": "qian",
    "白": "bai",
    "流": "liu",
    "泪": "lei",
    "披": "pi",
    "发": "fa",
    "王": "wang",
    "冠": "guan",
    "翅": "chi",
    "膀": "bang",
    "舌": "she",
    "头": "tou",
    "猫": "mao",
    "耳": "er",
    "手": "shou",
    "柄": "bing",
    "直": "zhi",
    "播": "bo",
    "套": "tao",
    "装": "zhuang",
    "马": "ma",
    "尾": "wei",
}
