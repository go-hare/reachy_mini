"""AskUserQuestionTool — collect structured input from the user.

Mirrors Claude Code's AskUserQuestionTool: the LLM can present
multiple-choice questions to gather information, clarify ambiguity,
or offer choices.

This is a **client-side** tool: the engine yields a ``PendingToolCallEvent``
and the host renders the question UI and submits the user's answer
via ``Agent.submit_tool_results()``.  For hosts that cannot render
custom UI, a console-based fallback is provided via ``ConsoleAskHandler``.
"""

from __future__ import annotations

from typing import Any

from ..tool import ClientTool

DESCRIPTION = (
    "Asks the user multiple-choice questions to gather information, "
    "clarify ambiguity, understand preferences, make decisions, or offer choices."
)

INSTRUCTIONS = """\
Use this tool when you need to ask the user questions during execution. \
This allows you to:
1. Gather user preferences or requirements
2. Clarify ambiguous instructions
3. Get decisions on implementation choices as you work
4. Offer choices to the user about what direction to take

Usage notes:
- Users can always provide custom text input beyond the listed options
- Use allow_multiple: true to allow multiple answers to be selected
- If you recommend a specific option, make it the first option and add \
"(Recommended)" at the end of the label
- Each question needs a unique id, a prompt, and at least 2 options
- Each option needs an id and a label\
"""

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "description": "Array of questions to present to the user.",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Unique identifier for this question.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The question text to display.",
                    },
                    "options": {
                        "type": "array",
                        "description": "Array of answer options (min 2).",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": "Unique id for this option.",
                                },
                                "label": {
                                    "type": "string",
                                    "description": "Display text for this option.",
                                },
                            },
                            "required": ["id", "label"],
                        },
                        "minItems": 2,
                    },
                    "allow_multiple": {
                        "type": "boolean",
                        "description": "If true, user can select multiple options.",
                        "default": False,
                    },
                },
                "required": ["id", "prompt", "options"],
            },
            "minItems": 1,
        },
    },
    "required": ["questions"],
}


class AskUserQuestionTool(ClientTool):
    """Structured multiple-choice question tool (client-side).

    The host application handles the actual UI rendering and
    result submission.
    """

    def __init__(self) -> None:
        super().__init__(
            name="AskUserQuestion",
            description=DESCRIPTION,
            parameters=PARAMETERS_SCHEMA,
        )
        self.instructions = INSTRUCTIONS
