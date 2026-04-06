"""Permission engine aligned to Claude Code's permission model."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .providers import BaseProvider

from .messages import user_message

logger = logging.getLogger(__name__)


class PermissionMode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    BYPASS = "bypassPermissions"
    PLAN = "plan"
    AUTO = "auto"
    DONT_ASK = "dontAsk"
    BUBBLE = "bubble"


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


SAFE_ALLOWLISTED_TOOLS: frozenset[str] = frozenset(
    {
        "Read",
        "Grep",
        "Glob",
        "LSP",
        "ToolSearch",
        "ListMcpResourcesTool",
        "ReadMcpResourceTool",
        "TodoWrite",
        "TaskCreate",
        "TaskGet",
        "TaskUpdate",
        "TaskList",
        "TaskStop",
        "AskUserQuestion",
        "EnterPlanMode",
        "ExitPlanMode",
        "VerifyPlanExecution",
        "Sleep",
        "TeamCreate",
        "TeamDelete",
        "SendMessage",
        "SyntheticOutput",
    }
)

DENIAL_WORKAROUND_GUIDANCE = (
    "IMPORTANT: You *may* attempt to accomplish this action using other tools that might "
    "naturally be used to accomplish this goal, e.g. using head instead of cat. But you "
    "*should not* attempt to work around this denial in malicious ways, e.g. do not use "
    "your ability to run tests to execute non-test actions. You should only try to work "
    "around this restriction in reasonable ways that do not attempt to bypass the intent "
    "behind this denial. If you believe this capability is essential to complete the user's "
    "request, STOP and explain to the user what you were trying to do and why you need this "
    "permission. Let the user decide how to proceed."
)

EDIT_ALLOWLISTED_TOOLS: frozenset[str] = frozenset(
    {
        "Edit",
        "Write",
        "NotebookEdit",
        "FileEdit",
        "FileWrite",
    }
)

DANGEROUS_BASH_PATTERNS: tuple[str, ...] = (
    "python",
    "python3",
    "python2",
    "node",
    "deno",
    "tsx",
    "ruby",
    "perl",
    "php",
    "lua",
    "npx",
    "bunx",
    "npm run",
    "yarn run",
    "pnpm run",
    "bun run",
    "bash",
    "sh",
    "zsh",
    "fish",
    "eval",
    "exec",
    "env",
    "xargs",
    "sudo",
    "ssh",
)

DESTRUCTIVE_PATTERNS: tuple[str, ...] = (
    "rm -rf",
    "rmdir /s",
    "del /f",
    "format ",
    "mkfs",
    "dd if=",
    "git reset --hard",
    "git clean -fd",
    "git push --force",
    "shutdown",
    "reboot",
    "drop database",
    "drop table",
    "truncate table",
)


def is_safe_tool(tool_name: str) -> bool:
    return tool_name in SAFE_ALLOWLISTED_TOOLS


def is_edit_tool(tool_name: str) -> bool:
    return tool_name in EDIT_ALLOWLISTED_TOOLS


def build_permission_denied_message(
    tool_name: str,
    *,
    mode: PermissionMode | str | None = None,
) -> str:
    current_mode = str(mode.value if isinstance(mode, PermissionMode) else mode or "")
    if current_mode == PermissionMode.DONT_ASK.value:
        prefix = (
            f"Permission to use {tool_name} has been denied because Claude Code is running in don't ask mode."
        )
    else:
        prefix = f"Permission to use {tool_name} has been denied."
    return f"{prefix} {DENIAL_WORKAROUND_GUIDANCE}"


def is_destructive_command(command: str) -> bool:
    command_lower = command.lower().strip()
    return any(pattern in command_lower for pattern in DESTRUCTIVE_PATTERNS)


def permission_rule_extract_prefix(permission_rule: str) -> str | None:
    match = re.match(r"^(.+):\*$", permission_rule)
    return match.group(1) if match else None


def has_wildcards(pattern: str) -> bool:
    if pattern.endswith(":*"):
        return False
    for i, char in enumerate(pattern):
        if char == "*":
            # Count backslashes before this asterisk
            backslash_count = 0
            j = i - 1
            while j >= 0 and pattern[j] == "\\":
                backslash_count += 1
                j -= 1
            # If even number of backslashes (including 0), the asterisk is unescaped
            if backslash_count % 2 == 0:
                return True
    return False


def match_wildcard_pattern(pattern: str, command: str, case_insensitive: bool = False) -> bool:
    trimmed = pattern.strip()
    escaped_star = "\x00ESCAPED_STAR\x00"
    escaped_backslash = "\x00ESCAPED_BACKSLASH\x00"

    processed: list[str] = []
    index = 0
    while index < len(trimmed):
        char = trimmed[index]
        if char == "\\" and index + 1 < len(trimmed):
            next_char = trimmed[index + 1]
            if next_char == "*":
                processed.append(escaped_star)
                index += 2
                continue
            if next_char == "\\":
                processed.append(escaped_backslash)
                index += 2
                continue
        processed.append(char)
        index += 1

    literal = "".join(processed)
    regex = re.escape(literal).replace(r"\*", ".*")
    regex = regex.replace(re.escape(escaped_star), r"\*")
    regex = regex.replace(re.escape(escaped_backslash), r"\\")

    if regex.endswith(r"\ .*") and literal.count("*") == 1:
        regex = regex[: -len(r"\ .*")] + r"( .*)?"

    flags = re.DOTALL | (re.IGNORECASE if case_insensitive else 0)
    return re.fullmatch(regex, command, flags=flags) is not None


@dataclass(slots=True)
class ParsedShellRule:
    type: str
    value: str


def parse_shell_permission_rule(permission_rule: str) -> ParsedShellRule:
    prefix = permission_rule_extract_prefix(permission_rule)
    if prefix is not None:
        return ParsedShellRule(type="prefix", value=prefix)
    if has_wildcards(permission_rule):
        return ParsedShellRule(type="wildcard", value=permission_rule)
    return ParsedShellRule(type="exact", value=permission_rule)


@dataclass(slots=True)
class PermissionRule:
    tool_pattern: str
    decision: PermissionDecision
    reason: str = ""

    @property
    def tool_name(self) -> str:
        if "(" in self.tool_pattern and self.tool_pattern.endswith(")"):
            return self.tool_pattern.split("(", 1)[0]
        return self.tool_pattern

    @property
    def rule_content(self) -> str | None:
        if "(" not in self.tool_pattern or not self.tool_pattern.endswith(")"):
            return None
        return self.tool_pattern[self.tool_pattern.find("(") + 1 : -1]

    def matches(self, tool_name: str, tool_input: dict[str, Any] | None = None) -> bool:
        if not fnmatchcase(tool_name, self.tool_name):
            return False

        rule_content = self.rule_content
        if rule_content is None:
            return True

        tool_input = tool_input or {}
        if tool_name in {"Bash", "PowerShell", "REPL"}:
            command = str(tool_input.get("command", "")).strip()
            if not command:
                return False
            parsed = parse_shell_permission_rule(rule_content)
            if parsed.type == "exact":
                return command == parsed.value
            if parsed.type == "prefix":
                return command == parsed.value or command.startswith(f"{parsed.value} ")
            return match_wildcard_pattern(parsed.value, command)

        if tool_name == "Agent":
            target = str(tool_input.get("subagent_type", "") or tool_input.get("role", "")).strip()
            return bool(target) and fnmatchcase(target, rule_content)

        raw_value = str(tool_input.get("file_path", "") or tool_input.get("path", "")).strip()
        if raw_value:
            return fnmatchcase(raw_value, rule_content)
        return False


def rule_matches(tool_pattern: str, tool_name: str, tool_input: dict[str, Any] | None = None) -> bool:
    return PermissionRule(tool_pattern=tool_pattern, decision=PermissionDecision.ASK).matches(
        tool_name,
        tool_input,
    )


def permission_rules_from_config(raw_rules: list[dict[str, str]] | None) -> list[PermissionRule]:
    if not raw_rules:
        return []
    valid = {decision.value for decision in PermissionDecision}
    parsed_rules: list[PermissionRule] = []
    for raw_rule in raw_rules:
        tool_pattern = str(raw_rule.get("tool_pattern", "")).strip()
        decision_raw = str(raw_rule.get("decision", "")).strip()
        reason = str(raw_rule.get("reason", "")).strip()
        if not tool_pattern or decision_raw not in valid:
            continue
        parsed_rules.append(
            PermissionRule(
                tool_pattern=tool_pattern,
                decision=PermissionDecision(decision_raw),
                reason=reason,
            )
        )
    return parsed_rules


def serialize_permission_rules(rules: list[PermissionRule]) -> list[dict[str, str]]:
    return [
        {
            "tool_pattern": rule.tool_pattern,
            "decision": rule.decision.value,
            "reason": rule.reason,
        }
        for rule in rules
    ]


PermissionCallback = Callable[[str, dict[str, Any]], Awaitable[PermissionDecision]]


@dataclass(slots=True)
class AutoModeState:
    active: bool = False
    circuit_broken: bool = False
    consecutive_failures: int = 0
    max_failures: int = 3
    total_classifications: int = 0
    total_allowed: int = 0
    total_blocked: int = 0

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_failures:
            self.circuit_broken = True

    def record_success(self, allowed: bool) -> None:
        self.consecutive_failures = 0
        self.total_classifications += 1
        if allowed:
            self.total_allowed += 1
        else:
            self.total_blocked += 1


@dataclass(slots=True)
class PermissionConfig:
    mode: PermissionMode = PermissionMode.DEFAULT
    rules: list[PermissionRule] = field(default_factory=list)
    auto_mode_allow_descriptions: list[str] = field(default_factory=list)
    auto_mode_deny_descriptions: list[str] = field(default_factory=list)


async def classify_auto_mode(
    provider: BaseProvider,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    config: PermissionConfig | None = None,
) -> tuple[PermissionDecision, str]:
    del config
    command = str(tool_input.get("command", "")).strip()
    if command and is_destructive_command(command):
        return PermissionDecision.DENY, "Matched destructive command pattern"
    if is_safe_tool(tool_name):
        return PermissionDecision.ALLOW, "Matched safe allowlist"
    if tool_name in {"Bash", "PowerShell", "REPL"}:
        lowered = command.lower()
        if any(lowered == p or lowered.startswith(f"{p} ") for p in DANGEROUS_BASH_PATTERNS):
            return PermissionDecision.ASK, "Matched dangerous shell prefix"

    try:
        prompt = (
            "Classify this tool execution for permissioning.\n"
            "Return exactly one word: allow, deny, or ask.\n\n"
            f"Tool: {tool_name}\n"
            f"Input: {tool_input}\n"
        )
        response = await provider.complete(
            messages=[user_message(prompt)],
            system="You are a strict permission classifier.",
            max_tokens=5,
            temperature=0.0,
            query_source="auto_mode",
        )
        text = response.text.strip().lower()
        if text in {"allow", "deny", "ask"}:
            return PermissionDecision(text), "Model classifier"
    except Exception as exc:
        logger.warning("Auto-mode classifier request failed: %s", exc)

    return PermissionDecision.ASK, "Classifier fallback"


class PermissionChecker:
    """Permission decision surface used by the runtime."""

    def __init__(
        self,
        config: PermissionConfig | None = None,
        *,
        ask_callback: PermissionCallback | None = None,
        classifier_provider: BaseProvider | None = None,
        project_dir: str | Any | None = None,
    ) -> None:
        del project_dir
        self._config = config or PermissionConfig()
        self._ask_callback = ask_callback
        self._classifier_provider = classifier_provider
        self._auto_state = AutoModeState(active=self._config.mode == PermissionMode.AUTO)
        # TS: denials tracking — records denied tool calls for system prompt injection
        self._denials: list[dict[str, Any]] = []

    @property
    def denials(self) -> list[dict[str, Any]]:
        """List of denied tool calls (tool_name, tool_input, reason)."""
        return list(self._denials)

    def record_denial(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        reason: str = "",
        tool_use_id: str | None = None,
    ) -> None:
        """Record a tool call denial for tracking (aligns with TS QueryEngine.permissionDenials)."""
        entry: dict[str, Any] = {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": tool_use_id or "",
            "reason": reason,
        }
        self._denials.append(entry)

    @property
    def mode(self) -> PermissionMode:
        return self._config.mode

    @property
    def auto_state(self) -> AutoModeState:
        return self._auto_state

    def set_mode(self, mode: PermissionMode) -> None:
        self._config.mode = mode
        self._auto_state.active = mode == PermissionMode.AUTO

    def _matching_rule(
        self,
        tool_name: str,
        tool_input: dict[str, Any] | None = None,
    ) -> PermissionRule | None:
        for rule in self._config.rules:
            if rule.matches(tool_name, tool_input):
                return rule
        return None

    def check(
        self,
        tool_name: str,
        *,
        is_read_only: bool = False,
        tool_input: dict[str, Any] | None = None,
    ) -> PermissionDecision:
        rule = self._matching_rule(tool_name, tool_input)
        if rule is not None:
            return rule.decision

        mode = self._config.mode
        if mode == PermissionMode.BYPASS:
            return PermissionDecision.ALLOW
        if mode == PermissionMode.PLAN:
            return PermissionDecision.ALLOW if is_read_only else PermissionDecision.DENY
        if mode == PermissionMode.ACCEPT_EDITS:
            if is_read_only or is_edit_tool(tool_name):
                return PermissionDecision.ALLOW
            return self._apply_dont_ask(PermissionDecision.ASK)
        if mode == PermissionMode.AUTO:
            if is_read_only or is_safe_tool(tool_name):
                return PermissionDecision.ALLOW
            return self._apply_dont_ask(PermissionDecision.ASK)
        # default / bubble / dontAsk all go through normal flow
        if is_read_only:
            return PermissionDecision.ALLOW
        return self._apply_dont_ask(PermissionDecision.ASK)

    def _apply_dont_ask(self, decision: PermissionDecision) -> PermissionDecision:
        """In dontAsk mode, convert ASK decisions to DENY."""
        if decision == PermissionDecision.ASK and self._config.mode == PermissionMode.DONT_ASK:
            return PermissionDecision.DENY
        return decision

    def _finalize_decision(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        decision: PermissionDecision,
        *,
        reason: str = "",
        tool_use_id: str | None = None,
    ) -> PermissionDecision:
        if decision == PermissionDecision.DENY:
            self.record_denial(
                tool_name,
                tool_input,
                reason=reason,
                tool_use_id=tool_use_id,
            )
        return decision

    async def resolve(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        is_read_only: bool = False,
        tool_use_id: str | None = None,
    ) -> PermissionDecision:
        rule = self._matching_rule(tool_name, tool_input)
        if rule is not None:
            return self._finalize_decision(
                tool_name,
                tool_input,
                rule.decision,
                reason="rule_match",
                tool_use_id=tool_use_id,
            )

        mode = self._config.mode
        if mode == PermissionMode.BYPASS:
            return PermissionDecision.ALLOW
        if mode == PermissionMode.PLAN:
            decision = PermissionDecision.ALLOW if is_read_only else PermissionDecision.DENY
            return self._finalize_decision(
                tool_name,
                tool_input,
                decision,
                reason="plan_mode",
                tool_use_id=tool_use_id,
            )

        command = str(tool_input.get("command", "")).strip()
        if command and is_destructive_command(command):
            return self._finalize_decision(
                tool_name,
                tool_input,
                PermissionDecision.DENY,
                reason="destructive_command",
                tool_use_id=tool_use_id,
            )

        if mode == PermissionMode.ACCEPT_EDITS:
            if is_read_only or is_edit_tool(tool_name):
                return PermissionDecision.ALLOW
            if self._ask_callback is not None:
                result = await self._ask_callback(tool_name, tool_input)
                final = self._apply_dont_ask(result)
                return self._finalize_decision(
                    tool_name,
                    tool_input,
                    final,
                    reason="ask_callback" if final == PermissionDecision.DENY else "",
                    tool_use_id=tool_use_id,
                )
            final = self._apply_dont_ask(PermissionDecision.ASK)
            return self._finalize_decision(
                tool_name,
                tool_input,
                final,
                reason="dont_ask_mode" if final == PermissionDecision.DENY else "",
                tool_use_id=tool_use_id,
            )

        if is_read_only or is_safe_tool(tool_name):
            return PermissionDecision.ALLOW

        if mode == PermissionMode.AUTO:
            if self._classifier_provider is None or self._auto_state.circuit_broken:
                final = self._apply_dont_ask(PermissionDecision.ASK)
                return self._finalize_decision(
                    tool_name,
                    tool_input,
                    final,
                    reason="auto_classifier_unavailable" if final == PermissionDecision.DENY else "",
                    tool_use_id=tool_use_id,
                )
            try:
                decision, _reason = await classify_auto_mode(
                    self._classifier_provider,
                    tool_name,
                    tool_input,
                    config=self._config,
                )
                self._auto_state.record_success(decision == PermissionDecision.ALLOW)
                final = self._apply_dont_ask(decision)
                return self._finalize_decision(
                    tool_name,
                    tool_input,
                    final,
                    reason=_reason if final == PermissionDecision.DENY else "",
                    tool_use_id=tool_use_id,
                )
            except Exception as exc:
                logger.error("Auto-mode classifier failed: %s", exc)
                self._auto_state.record_failure()
                final = self._apply_dont_ask(PermissionDecision.ASK)
                return self._finalize_decision(
                    tool_name,
                    tool_input,
                    final,
                    reason="auto_classifier_error" if final == PermissionDecision.DENY else "",
                    tool_use_id=tool_use_id,
                )

        if self._ask_callback is not None:
            result = await self._ask_callback(tool_name, tool_input)
            final = self._apply_dont_ask(result)
            return self._finalize_decision(
                tool_name,
                tool_input,
                final,
                reason="ask_callback" if final == PermissionDecision.DENY else "",
                tool_use_id=tool_use_id,
            )
        final = self._apply_dont_ask(PermissionDecision.ASK)
        return self._finalize_decision(
            tool_name,
            tool_input,
            final,
            reason="dont_ask_mode" if final == PermissionDecision.DENY else "",
            tool_use_id=tool_use_id,
        )


def build_permission_checker(
    *,
    mode: PermissionMode | str = PermissionMode.DEFAULT,
    raw_rules: list[dict[str, str]] | None = None,
    classifier_provider: BaseProvider | None = None,
    project_dir: str | Any | None = None,
) -> PermissionChecker:
    permission_mode = mode if isinstance(mode, PermissionMode) else PermissionMode(mode)
    return PermissionChecker(
        config=PermissionConfig(
            mode=permission_mode,
            rules=permission_rules_from_config(raw_rules),
        ),
        classifier_provider=classifier_provider if permission_mode == PermissionMode.AUTO else None,
        project_dir=project_dir,
    )
