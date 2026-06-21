"""Native Live2D avatar actions."""

from __future__ import annotations

from typing import Any

from reachy_mini.action_runtime import ActionContext, ActionMetadata, ActionSpec
from reachy_mini.runtime.live2d_avatar import (
    Live2DCapabilities,
    Live2DNativeAction,
    live2d_tool_name,
)

from .common import BaseRobotAction


def create_live2d_registry_actions(
    capabilities: Live2DCapabilities,
) -> tuple[tuple[ActionMetadata, Any], ...]:
    """Return one concrete tool per native Live2D model capability."""

    actions: list[tuple[ActionMetadata, Any]] = []
    used_names: set[str] = set()
    for motion in _iter_action_details(
        capabilities.motion_details,
        capabilities.motions,
        suffix=".motion3.json",
    ):
        tool_name = _unique_tool_name("motion", motion.name, used_names)
        actions.append(
            _build_tool(
                tool_name=tool_name,
                native_name=motion.name,
                event_action="live2d_motion",
                description=_tool_description(
                    capabilities,
                    tool_name=tool_name,
                    detail=motion,
                    kind="motion",
                ),
                priority=60,
                duration_s=motion.duration_s or 3.0,
                tags=["live2d", "avatar", "motion", motion.name, *motion.aliases],
            )
        )
    for expression in _iter_action_details(
        capabilities.expression_details,
        capabilities.expressions,
        suffix=".exp3.json",
    ):
        tool_name = _unique_tool_name("expression", expression.name, used_names)
        actions.append(
            _build_tool(
                tool_name=tool_name,
                native_name=expression.name,
                event_action="live2d_expression",
                description=_tool_description(
                    capabilities,
                    tool_name=tool_name,
                    detail=expression,
                    kind="expression",
                ),
                priority=55,
                duration_s=1.0,
                tags=[
                    "live2d",
                    "avatar",
                    "expression",
                    expression.name,
                    *expression.aliases,
                ],
            )
        )
    return tuple(actions)


def _build_tool(
    *,
    tool_name: str,
    native_name: str,
    event_action: str,
    description: str,
    priority: int,
    duration_s: float,
    tags: list[str],
) -> tuple[ActionMetadata, Any]:
    metadata = ActionMetadata(
        name=tool_name,
        description=description,
        parameter_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        required_locks=frozenset(),
        default_priority=priority,
        default_interruptible=True,
        default_duration_s=duration_s,
        tags=tags,
    )
    return (
        metadata,
        lambda spec, native_name=native_name, event_action=event_action: Live2DAction(
            spec,
            event_action,
            native_name=native_name,
        ),
    )


def _iter_action_details(
    details: tuple[Live2DNativeAction, ...],
    names: tuple[str, ...],
    *,
    suffix: str,
) -> tuple[Live2DNativeAction, ...]:
    if details:
        return details
    return tuple(
        Live2DNativeAction(name=name, file_name=f"{name}{suffix}") for name in names
    )


def _tool_description(
    capabilities: Live2DCapabilities,
    *,
    tool_name: str,
    detail: Live2DNativeAction,
    kind: str,
) -> str:
    file_type = ".motion3.json" if kind == "motion" else ".exp3.json"
    verb = "Play" if kind == "motion" else "Apply"
    noun = "motion" if kind == "motion" else "expression"
    aliases = ", ".join(detail.aliases) if detail.aliases else detail.name
    parameters = _format_parameters(detail)
    description = (
        f"{verb} the exact native Live2D {file_type} {noun} from the "
        f"{capabilities.model_name} model. Tool: {tool_name}. "
        f"Native name: {detail.name}. Source file: {detail.file_name}. "
        f"Meaning and request aliases: {aliases}."
    )
    if parameters:
        description = f"{description} Model parameters: {parameters}."
    description = (
        f"{description} If the user asks for any alias above, call this tool. "
        "This action already exists in the Live2D model; do not claim a developer "
        "must add it."
    )
    if detail.name == "HuiShou":
        description = (
            f"{description} Important: HuiShou is the model's real 挥手 / 举手 / "
            "招手 / 抬手 gesture."
        )
    return description


def _format_parameters(detail: Live2DNativeAction) -> str:
    if not detail.parameter_ids:
        return ""
    labels = list(detail.parameter_labels)
    parts: list[str] = []
    for index, parameter_id in enumerate(detail.parameter_ids):
        label = ""
        if len(labels) == 1:
            label = labels[0]
        elif index < len(labels):
            label = labels[index]
        parts.append(f"{parameter_id}({label})" if label else parameter_id)
    return ", ".join(parts)


def _unique_tool_name(kind: str, native_name: str, used_names: set[str]) -> str:
    base = live2d_tool_name(kind, native_name)
    candidate = base
    suffix = 2
    while candidate in used_names:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used_names.add(candidate)
    return candidate


class Live2DAction(BaseRobotAction):
    """Emit one native Live2D avatar event for the browser renderer."""

    def __init__(
        self,
        spec: ActionSpec,
        event_action: str,
        *,
        native_name: str,
    ) -> None:
        self.event_action = event_action
        self.native_name = native_name
        super().__init__(
            spec,
            required_locks=set(),
            duration_s=None,
            priority=60,
            interruptible=True,
        )

    async def run(self, context: ActionContext) -> dict[str, Any]:
        """Return a structured avatar event for ActionDispatcher publication."""

        await context.cancel_token.checkpoint()
        return {
            "embodiment": {
                "action": self.event_action,
                "target": "live2d",
                "payload": {"name": self.native_name},
            }
        }
