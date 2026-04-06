"""ConfigTool — inspect and update mini-agent configuration."""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Any

from ..config import CLIConfig, load_config, save_global_config
from ..paths import mini_agent_home
from ..tool import Tool, ToolUseContext


def _editable_keys() -> list[str]:
    return sorted(field.name for field in fields(CLIConfig))


def _coerce_value(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw

    text = raw.strip()
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        if text.startswith("{") or text.startswith("["):
            return json.loads(text)
    except json.JSONDecodeError:
        pass
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        try:
            return int(text)
        except ValueError:
            return text
    return text


class ConfigTool(Tool):
    name = "Config"
    description = "Read or update mini-agent configuration settings."
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        keys = _editable_keys()
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "set", "list"],
                    "description": "Configuration operation to perform.",
                },
                "key": {
                    "type": "string",
                    "enum": keys,
                    "description": "Configuration field name.",
                },
                "value": {
                    "description": "New value for the field when action=set.",
                },
            },
            "required": ["action"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        action = kwargs["action"]
        cfg = load_config()

        if action == "list":
            data = {key: getattr(cfg, key) for key in _editable_keys()}
            return json.dumps(
                {"path": str(mini_agent_home() / "config.json"), "config": data},
                indent=2,
                ensure_ascii=False,
            )

        key = kwargs.get("key", "")
        if not key:
            return "Error: 'key' is required for get/set actions."

        if action == "get":
            return json.dumps(
                {"key": key, "value": getattr(cfg, key)},
                indent=2,
                ensure_ascii=False,
            )

        if "value" not in kwargs:
            return "Error: 'value' is required for action=set."

        coerced = _coerce_value(kwargs["value"])
        save_global_config({key: coerced})
        return json.dumps(
            {"path": str(mini_agent_home() / "config.json"), "updated": {key: coerced}},
            indent=2,
            ensure_ascii=False,
        )
