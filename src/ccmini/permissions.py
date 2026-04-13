"""Permission engine aligned to Claude Code's permission model."""

from __future__ import annotations

import logging
import os
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatchcase
from pathlib import Path
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

SAFE_READONLY_BASH_COMMANDS: frozenset[str] = frozenset(
    {
        "basename",
        "cat",
        "cmp",
        "column",
        "comm",
        "cut",
        "date",
        "df",
        "diff",
        "dirname",
        "du",
        "expr",
        "fd",
        "fdfind",
        "file",
        "find",
        "fmt",
        "fold",
        "free",
        "getconf",
        "git",
        "grep",
        "groups",
        "head",
        "hexdump",
        "id",
        "locale",
        "ls",
        "nl",
        "nproc",
        "numfmt",
        "od",
        "paste",
        "printf",
        "pwd",
        "readlink",
        "realpath",
        "rev",
        "rg",
        "sed",
        "seq",
        "sleep",
        "sort",
        "stat",
        "strings",
        "tail",
        "tac",
        "test",
        "tr",
        "true",
        "tsort",
        "uname",
        "uniq",
        "unexpand",
        "wc",
        "which",
        "whoami",
        "echo",
    }
)

SAFE_READONLY_GIT_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "blame",
        "branch",
        "config",
        "describe",
        "diff",
        "grep",
        "log",
        "ls-files",
        "merge-base",
        "remote",
        "reflog",
        "rev-parse",
        "show",
        "status",
        "tag",
    }
)

READONLY_BASH_BLOCKED_FLAGS: dict[str, frozenset[str]] = {
    "diff": frozenset({"-o", "--output"}),
    "find": frozenset({"-delete", "-exec", "-execdir", "-fprint", "-fprintf", "-fls", "-ok", "-okdir"}),
    "git": frozenset({"-c", "--config-env", "--exec-path", "--git-dir", "--output", "--work-tree"}),
    "printf": frozenset({"-v"}),
    "rg": frozenset({"--pre"}),
    "sed": frozenset({"-i"}),
    "sort": frozenset({"-o", "--output"}),
}

SAFE_GIT_CONFIG_QUERY_FLAGS: frozenset[str] = frozenset({"--get", "--get-all", "--get-regexp", "--list", "-l"})
SAFE_GIT_CONFIG_SCOPE_FLAGS: frozenset[str] = frozenset({"--global", "--system", "--local", "--worktree"})
SAFE_GIT_BRANCH_FLAGS: frozenset[str] = frozenset({"-a", "--all", "-r", "--remotes", "--show-current", "-v", "-vv", "--list"})
SAFE_GIT_TAG_FLAGS: frozenset[str] = frozenset({"-l", "--list"})
SAFE_GIT_REMOTE_FLAGS: frozenset[str] = frozenset({"-v", "--verbose"})

REVIEW_SHELL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"`"),
    re.compile(r"\$\("),
    re.compile(r"\$\{"),
    re.compile(r"<<"),
    re.compile(r"(^|[^\\])[<>]"),
    re.compile(r"(^|[^\\]);"),
    re.compile(r"(^|[^\\])\|\|"),
    re.compile(r"(?<![\\&])&(?!&)"),
)


class RiskLevel(str, Enum):
    SAFE = "safe"
    NEEDS_REVIEW = "needs_review"
    DANGEROUS = "dangerous"
    BLOCKED = "blocked"


@dataclass(slots=True)
class ShellRedirection:
    kind: str
    operator: str
    target: str = ""


def _scan_shell_redirection(command: str) -> ShellRedirection | None:
    quote = ""
    escape = False
    index = 0
    while index < len(command):
        char = command[index]
        if escape:
            escape = False
            index += 1
            continue
        if quote:
            if char == quote:
                quote = ""
            elif char == "\\" and quote == '"':
                escape = True
            index += 1
            continue
        if char == "\\":
            escape = True
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue

        if char == "<":
            if index + 2 < len(command) and command[index + 1] == "<" and command[index + 2] == "<":
                return ShellRedirection(kind="input", operator="<<<")
            if index + 1 < len(command) and command[index + 1] == "<":
                return ShellRedirection(kind="input", operator="<<")
            return ShellRedirection(kind="input", operator="<")

        if char == ">":
            target = _extract_redirection_target(command, index + 1)
            if index > 0 and command[index - 1].isdigit():
                return ShellRedirection(kind="output", operator=f"{command[index - 1]}>", target=target)
            operator = ">>" if index + 1 < len(command) and command[index + 1] == ">" else ">"
            return ShellRedirection(kind="output", operator=operator, target=target)

        if char == "&" and index + 1 < len(command) and command[index + 1] == ">":
            target = _extract_redirection_target(command, index + 2)
            return ShellRedirection(kind="output", operator="&>", target=target)

        index += 1
    return None


def _extract_redirection_target(command: str, start: int) -> str:
    index = start
    length = len(command)
    if index < length and command[index] == ">":
        index += 1
    while index < length and command[index].isspace():
        index += 1
    token: list[str] = []
    quote = ""
    escape = False
    while index < length:
        char = command[index]
        if escape:
            token.append(char)
            escape = False
            index += 1
            continue
        if quote:
            token.append(char)
            if char == quote:
                quote = ""
            elif char == "\\" and quote == '"':
                escape = True
            index += 1
            continue
        if char == "\\":
            token.append(char)
            escape = True
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            token.append(char)
            index += 1
            continue
        if char.isspace() or char in {"|", "&", ";", "<", ">"}:
            break
        token.append(char)
        index += 1
    return "".join(token).strip()


def _split_shell_command_for_analysis(command: str) -> tuple[list[str], list[str]] | None:
    segments: list[str] = []
    operators: list[str] = []
    current: list[str] = []
    quote = ""
    escape = False

    index = 0
    while index < len(command):
        char = command[index]
        if escape:
            current.append(char)
            escape = False
            index += 1
            continue

        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            elif char == "\\" and quote == '"':
                escape = True
            index += 1
            continue

        if char == "\\":
            current.append(char)
            escape = True
            index += 1
            continue

        if char in {"'", '"'}:
            quote = char
            current.append(char)
            index += 1
            continue

        if char == "&" and index + 1 < len(command) and command[index + 1] == "&":
            segment = "".join(current).strip()
            if not segment:
                return None
            segments.append(segment)
            operators.append("&&")
            current = []
            index += 2
            continue

        if char == "|":
            if index + 1 < len(command) and command[index + 1] == "|":
                return None
            segment = "".join(current).strip()
            if not segment:
                return None
            segments.append(segment)
            operators.append("|")
            current = []
            index += 1
            continue

        if char in {";", "<", ">"}:
            return None

        current.append(char)
        index += 1

    if quote or escape:
        return None

    tail = "".join(current).strip()
    if not tail:
        return None
    segments.append(tail)
    return segments, operators


def _tokens_require_review(tokens: list[str]) -> bool:
    for token in tokens:
        if "$" in token:
            return True
        if "{" in token and ("," in token or ".." in token):
            return True
    return False


def _is_safe_readonly_git(args: list[str]) -> bool:
    if not args:
        return False
    subcommand = args[0].lower()
    if subcommand not in SAFE_READONLY_GIT_SUBCOMMANDS:
        return False
    blocked_flags = {
        "--exec-path",
        "--git-dir",
        "--output",
        "--work-tree",
    }
    for token in args[1:]:
        lowered = token.lower()
        if lowered in blocked_flags:
            return False
        if any(lowered.startswith(f"{flag}=") for flag in blocked_flags):
            return False
        if "$" in token:
            return False
    if subcommand == "config":
        return _is_safe_readonly_git_config(args[1:])
    if subcommand == "branch":
        return _is_safe_readonly_git_branch(args[1:])
    if subcommand == "tag":
        return _is_safe_readonly_git_tag(args[1:])
    if subcommand == "remote":
        return _is_safe_readonly_git_remote(args[1:])
    return True


def _is_safe_readonly_git_config(args: list[str]) -> bool:
    if not args:
        return False
    query_seen = False
    index = 0
    while index < len(args):
        token = args[index]
        lowered = token.lower()
        if lowered in SAFE_GIT_CONFIG_SCOPE_FLAGS:
            index += 1
            continue
        if lowered in {"--type"}:
            if index + 1 >= len(args):
                return False
            index += 2
            continue
        if lowered.startswith("--type="):
            index += 1
            continue
        if lowered in {"--get", "--get-all", "--get-regexp"}:
            query_seen = True
            if index + 1 >= len(args):
                return False
            index += 2
            continue
        if lowered in SAFE_GIT_CONFIG_QUERY_FLAGS:
            query_seen = True
            index += 1
            continue
        return False
    return query_seen


def _is_safe_readonly_git_branch(args: list[str]) -> bool:
    if not args:
        return True
    return all(token.lower() in SAFE_GIT_BRANCH_FLAGS for token in args)


def _is_safe_readonly_git_tag(args: list[str]) -> bool:
    if not args:
        return True
    return all(token.lower() in SAFE_GIT_TAG_FLAGS for token in args)


def _is_safe_readonly_git_remote(args: list[str]) -> bool:
    if not args:
        return True
    return all(token.lower() in SAFE_GIT_REMOTE_FLAGS for token in args)


def _contains_blocked_flag(command: str, tokens: list[str]) -> bool:
    blocked_flags = READONLY_BASH_BLOCKED_FLAGS.get(command)
    if not blocked_flags:
        return False
    for token in tokens[1:]:
        lowered = token.lower()
        if lowered in blocked_flags:
            return True
        if any(lowered.startswith(f"{flag}=") for flag in blocked_flags):
            return True
    return False


def _is_safe_readonly_bash_segment(segment: str) -> bool:
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return False
    if not tokens:
        return False
    if _tokens_require_review(tokens):
        return False

    command = tokens[0].lower()
    if command not in SAFE_READONLY_BASH_COMMANDS:
        return False
    if _contains_blocked_flag(command, tokens):
        return False

    if command in {"echo", "printf"}:
        return True
    if command == "git":
        return _is_safe_readonly_git(tokens[1:])
    return True


class BashCommandAnalyzer:
    """Best-effort bash safety classifier inspired by donor readonly validation."""

    @staticmethod
    def classify(command: str) -> tuple[RiskLevel, str]:
        text = str(command or "").strip()
        if not text:
            return RiskLevel.BLOCKED, "Empty command"
        if is_destructive_command(text):
            return RiskLevel.DANGEROUS, "Matched destructive command pattern"
        if redirection := _scan_shell_redirection(text):
            if redirection.kind == "output":
                target = f" ({redirection.target})" if redirection.target else ""
                return RiskLevel.DANGEROUS, f"Output redirection{target} can write files"
            return RiskLevel.NEEDS_REVIEW, "Input redirection requires review"

        lowered = text.lower()
        if any(lowered == prefix or lowered.startswith(f"{prefix} ") for prefix in DANGEROUS_BASH_PATTERNS):
            return RiskLevel.NEEDS_REVIEW, "Matched dangerous shell/runtime prefix"
        if any(pattern.search(text) for pattern in REVIEW_SHELL_PATTERNS):
            return RiskLevel.NEEDS_REVIEW, "Uses shell syntax that requires review"

        split_result = _split_shell_command_for_analysis(text)
        if split_result is None:
            return RiskLevel.NEEDS_REVIEW, "Could not validate shell structure"

        segments, _operators = split_result
        if all(_is_safe_readonly_bash_segment(segment) for segment in segments):
            return RiskLevel.SAFE, "Matched readonly bash allowlist"
        return RiskLevel.NEEDS_REVIEW, "Command is outside the readonly bash allowlist"


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


def _extract_requested_path(tool_input: dict[str, Any] | None) -> str:
    tool_input = tool_input or {}
    for key in ("file_path", "path"):
        raw = str(tool_input.get(key, "") or "").strip()
        if raw:
            return raw
    return ""


def _contains_path_glob(raw_path: str) -> bool:
    return any(char in raw_path for char in "*?[]")


def _normalize_scope_path(raw_path: str, base_dir: str) -> Path | None:
    value = str(raw_path or "").strip()
    if not value or _contains_path_glob(value):
        return None
    try:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            root = Path(base_dir or os.getcwd()).expanduser().resolve()
            candidate = root / candidate
        return candidate.resolve()
    except OSError:
        return None


def _is_subpath(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


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
    project_dir: str = ""
    additional_directories: list[str] = field(default_factory=list)


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

    @property
    def project_dir(self) -> str:
        return str(self._config.project_dir or "").strip()

    @property
    def additional_directories(self) -> list[str]:
        return list(self._config.additional_directories)

    def set_additional_directories(self, paths: list[str]) -> None:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_path in paths:
            try:
                resolved = str(Path(str(raw_path)).expanduser().resolve())
            except OSError:
                continue
            if not resolved or resolved in seen:
                continue
            seen.add(resolved)
            normalized.append(resolved)
        self._config.additional_directories = normalized

    def add_additional_directories(self, paths: list[str]) -> None:
        self.set_additional_directories(
            [*self._config.additional_directories, *paths],
        )

    def add_rules(self, rules: list[PermissionRule]) -> None:
        self._config.rules.extend(rules)

    def _is_path_within_allowed_workspace(
        self,
        tool_input: dict[str, Any] | None,
    ) -> bool:
        raw_path = _extract_requested_path(tool_input)
        if not raw_path:
            return True

        requested_path = _normalize_scope_path(raw_path, self.project_dir)
        if requested_path is None:
            return True

        scope_roots: list[Path] = []
        if self.project_dir:
            try:
                scope_roots.append(Path(self.project_dir).expanduser().resolve())
            except OSError:
                pass
        for raw_dir in self._config.additional_directories:
            try:
                scope_roots.append(Path(raw_dir).expanduser().resolve())
            except OSError:
                continue

        if not scope_roots:
            return True

        return any(_is_subpath(requested_path, root) for root in scope_roots)

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
        if is_read_only and not self._is_path_within_allowed_workspace(tool_input):
            return self._apply_dont_ask(PermissionDecision.ASK)
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

        if is_read_only and not self._is_path_within_allowed_workspace(tool_input):
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
    additional_dirs: list[str] | None = None,
    ask_callback: PermissionCallback | None = None,
) -> PermissionChecker:
    permission_mode = mode if isinstance(mode, PermissionMode) else PermissionMode(mode)
    return PermissionChecker(
        config=PermissionConfig(
            mode=permission_mode,
            rules=permission_rules_from_config(raw_rules),
            project_dir=str(project_dir or ""),
            additional_directories=list(additional_dirs or []),
        ),
        ask_callback=ask_callback,
        classifier_provider=classifier_provider if permission_mode == PermissionMode.AUTO else None,
        project_dir=project_dir,
    )
