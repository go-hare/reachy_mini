"""SkillTool — look up and execute prompt-style commands as helper skills."""

from __future__ import annotations

import json
from typing import Any

from ..commands import CommandRegistry
from ..commands.types import Command, CommandSource, CommandType
from ..delegation.multi_agent import SubAgentConfig, run_sub_agent
from ..providers import BaseProvider
from ..tool import Tool, ToolUseContext


class SkillTool(Tool):
    name = "Skill"
    description = "Find or execute a prompt-style skill or skill-like command."
    is_read_only = False

    def __init__(
        self,
        *,
        provider: BaseProvider,
        registry: CommandRegistry,
        parent_tools: list[Tool] | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._parent_tools = list(parent_tools or [])

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "describe", "run"],
                    "description": "Skill operation to perform.",
                },
                "query": {
                    "type": "string",
                    "description": "Search text or exact skill name.",
                },
                "arguments": {
                    "type": "string",
                    "description": "Optional arguments for the skill.",
                    "default": "",
                },
            },
            "required": ["action", "query"],
        }

    def _skills(self) -> list[Command]:
        return [
            command
            for command in self._registry.get_all_commands()
            if command.type == CommandType.PROMPT
            and command.source != CommandSource.BUILTIN
        ]

    def _find_matches(self, query: str) -> list[Command]:
        needle = query.strip().lower()
        matches: list[Command] = []
        for command in self._skills():
            haystacks = [
                command.name.lower(),
                command.description.lower(),
                command.when_to_use.lower(),
            ]
            if any(needle in item for item in haystacks):
                matches.append(command)
                continue
            aliases = {alias.lower() for alias in command.aliases}
            if needle in aliases:
                matches.append(command)
        return matches

    def _resolve_tools(self, command: Command) -> list[Tool]:
        if not command.allowed_tools:
            return list(self._parent_tools)
        allowed = set(command.allowed_tools)
        return [tool for tool in self._parent_tools if tool.name in allowed]

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        action = kwargs["action"]
        query = kwargs["query"].strip()
        arguments = kwargs.get("arguments", "").strip()
        matches = self._find_matches(query)

        if not matches:
            return f"No matching skills found for: {query}"

        if action == "search":
            data = [
                {
                    "name": command.name,
                    "description": command.description,
                    "when_to_use": command.when_to_use,
                    "source": command.source.value,
                }
                for command in matches[:10]
            ]
            return json.dumps({"matches": data}, indent=2, ensure_ascii=False)

        command = next(
            (item for item in matches if item.name.lower() == query.lower()),
            matches[0],
        )

        if action == "describe":
            return json.dumps(
                {
                    "name": command.name,
                    "description": command.description,
                    "when_to_use": command.when_to_use,
                    "allowed_tools": command.allowed_tools,
                    "model": command.model,
                    "source": command.source.value,
                    "prompt_text": command.prompt_text,
                },
                indent=2,
                ensure_ascii=False,
            )

        parent_messages = context.extras.get("messages")
        if not isinstance(parent_messages, list):
            parent_messages = None

        skill_prompt = command.prompt_text.strip() or f"Run the '{command.name}' skill."
        user_prompt = arguments or f"Apply the '{command.name}' skill to help with the current task."
        result = await run_sub_agent(
            provider=self._provider,
            config=SubAgentConfig(
                name=f"skill-{command.name}",
                role=command.agent or command.name,
                system_prompt=skill_prompt,
                max_turns=6,
                tools=self._resolve_tools(command),
                model=command.model,
            ),
            prompt=user_prompt,
            context_messages=parent_messages,
        )
        if result.success:
            return result.reply
        return f"Skill '{command.name}' failed: {result.error}"
