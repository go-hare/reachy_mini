"""User-configurable hook scripts — port of Claude Code's ``utils/hooks/``.

Discovers hook configs from settings, executes shell commands or HTTP
hooks at lifecycle events, with matcher filtering, exit-code semantics,
stdin JSON piping, timeout handling, and SSRF protection.

Hook configuration (in ``~/.ccmini/hooks.json`` or project
``.ccmini/hooks.json``)::

    {
      "hooks": {
        "PreToolUse": [
          {
            "matcher": "Bash|FileWrite",
            "hooks": [
              {"type": "command", "command": "my-guard.sh", "timeout": 30}
            ]
          }
        ],
        "PostToolUse": [
          {
            "matcher": "*",
            "hooks": [
              {"type": "http", "url": "https://example.com/hook"}
            ]
          }
        ]
      }
    }

Exit-code semantics (for ``command`` hooks):
- **0** — success; stdout optionally parsed as ``HookOutput`` JSON
- **2** — blocking; stderr shown to model, tool call blocked (PreToolUse)
- **other** — non-blocking error; stderr shown to user only
"""

from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..paths import mini_agent_path

logger = logging.getLogger(__name__)

# ── Hook events ──────────────────────────────────────────────────────


class HookEvent(str, Enum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    PERMISSION_DENIED = "PermissionDenied"
    PERMISSION_REQUEST = "PermissionRequest"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    STOP = "Stop"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    NOTIFICATION = "Notification"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    STOP_FAILURE = "StopFailure"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    SETUP = "Setup"
    TEAMMATE_IDLE = "TeammateIdle"
    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"
    ELICITATION = "Elicitation"
    ELICITATION_RESULT = "ElicitationResult"
    INSTRUCTIONS_LOADED = "InstructionsLoaded"
    WORKTREE_CREATE = "WorktreeCreate"
    WORKTREE_REMOVE = "WorktreeRemove"
    CONFIG_CHANGE = "ConfigChange"
    CWD_CHANGED = "CwdChanged"
    FILE_CHANGED = "FileChanged"


# ── Hook types ───────────────────────────────────────────────────────


class HookType(str, Enum):
    COMMAND = "command"
    HTTP = "http"


@dataclass(slots=True)
class HookSpec:
    type: HookType
    command: str = ""
    url: str = ""
    timeout: float = 600.0  # 10 min default
    is_async: bool = False
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class HookMatcher:
    matcher: str = "*"
    hooks: list[HookSpec] = field(default_factory=list)

    def matches(self, tool_name: str) -> bool:
        if not self.matcher or self.matcher == "*":
            return True
        # Simple string or pipe-separated list (no regex special chars except |)
        if re.fullmatch(r"[a-zA-Z0-9_|]+", self.matcher):
            if "|" in self.matcher:
                patterns = [p.strip() for p in self.matcher.split("|")]
                return tool_name in patterns
            return tool_name == self.matcher
        # Otherwise treat as regex
        try:
            if re.search(self.matcher, tool_name):
                return True
        except re.error:
            logger.debug("Invalid regex pattern in hook matcher: %s", self.matcher)
        return False


# ── Hook input/output ────────────────────────────────────────────────


@dataclass(slots=True)
class HookInput:
    session_id: str = ""
    transcript_path: str = ""
    cwd: str = ""
    hook_event_name: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_output: str = ""
    error: str = ""
    permission_mode: str = ""
    agent_id: str = ""
    agent_type: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        d: dict[str, Any] = {
            "session_id": self.session_id,
            "cwd": self.cwd,
            "hook_event_name": self.hook_event_name,
        }
        if self.transcript_path:
            d["transcript_path"] = self.transcript_path
        if self.tool_name:
            d["tool_name"] = self.tool_name
        if self.tool_input:
            d["tool_input"] = self.tool_input
        if self.tool_output:
            d["tool_output"] = self.tool_output
        if self.error:
            d["error"] = self.error
        if self.permission_mode:
            d["permission_mode"] = self.permission_mode
        if self.agent_id:
            d["agent_id"] = self.agent_id
        if self.agent_type:
            d["agent_type"] = self.agent_type
        d.update(self.extra)
        return json.dumps(d, ensure_ascii=False)


@dataclass(slots=True)
class HookOutput:
    """Parsed output from a hook execution."""
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    blocking: bool = False
    decision: str = ""       # "approve" | "block" | ""
    reason: str = ""
    system_message: str = ""
    should_continue: bool = True
    suppress_output: bool = False
    stop_reason: str = ""
    permission_behavior: str = ""  # "allow" | "deny" | "ask" | "passthrough" | ""
    additional_context: str = ""
    initial_user_message: str = ""
    updated_input: dict[str, Any] | None = None
    updated_mcp_tool_output: Any = None
    permission_request_result: dict[str, Any] | None = None
    retry: bool | None = None
    watch_paths: list[str] | None = None
    hook_specific_output: dict[str, Any] | None = None
    raw_json: dict[str, Any] = field(default_factory=dict)


def _json_field(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _apply_hook_json(output: HookOutput, parsed: dict[str, Any]) -> HookOutput:
    output.raw_json = parsed
    output.decision = str(_json_field(parsed, "decision") or output.decision)
    output.reason = str(_json_field(parsed, "reason") or output.reason)

    system_message = _json_field(parsed, "systemMessage", "system_message")
    if isinstance(system_message, str):
        output.system_message = system_message

    continue_value = _json_field(parsed, "continue", "shouldContinue", "should_continue")
    if continue_value is False:
        output.should_continue = False

    additional_context = _json_field(parsed, "additionalContext", "additional_context")
    if isinstance(additional_context, str):
        output.additional_context = additional_context

    stop_reason = _json_field(parsed, "stopReason", "stop_reason")
    if isinstance(stop_reason, str):
        output.stop_reason = stop_reason

    permission_behavior = _json_field(parsed, "permissionBehavior", "permission_behavior")
    if isinstance(permission_behavior, str):
        output.permission_behavior = permission_behavior

    initial_user_message = _json_field(parsed, "initialUserMessage", "initial_user_message")
    if isinstance(initial_user_message, str):
        output.initial_user_message = initial_user_message

    updated_input = _json_field(parsed, "updatedInput", "updated_input")
    if isinstance(updated_input, dict):
        output.updated_input = updated_input

    permission_request_result = _json_field(
        parsed,
        "permissionRequestResult",
        "permission_request_result",
    )
    if isinstance(permission_request_result, dict):
        output.permission_request_result = permission_request_result

    retry_value = _json_field(parsed, "retry")
    if isinstance(retry_value, bool):
        output.retry = retry_value

    watch_paths = _json_field(parsed, "watchPaths", "watch_paths")
    if isinstance(watch_paths, list):
        output.watch_paths = [str(path) for path in watch_paths]

    hook_specific_output = _json_field(parsed, "hookSpecificOutput", "hook_specific_output")
    if isinstance(hook_specific_output, dict):
        output.hook_specific_output = hook_specific_output

        permission_decision = _json_field(
            hook_specific_output,
            "permissionDecision",
            "permission_decision",
        )
        if permission_decision == "allow":
            output.permission_behavior = "allow"
        elif permission_decision == "deny":
            output.permission_behavior = "deny"
            output.blocking = True
        elif permission_decision == "ask":
            output.permission_behavior = "ask"

        permission_decision_reason = _json_field(
            hook_specific_output,
            "permissionDecisionReason",
            "permission_decision_reason",
        )
        if isinstance(permission_decision_reason, str) and permission_decision_reason:
            output.reason = permission_decision_reason

        nested_updated_input = _json_field(
            hook_specific_output,
            "updatedInput",
            "updated_input",
        )
        if isinstance(nested_updated_input, dict):
            output.updated_input = nested_updated_input

        nested_additional_context = _json_field(
            hook_specific_output,
            "additionalContext",
            "additional_context",
        )
        if isinstance(nested_additional_context, str):
            output.additional_context = nested_additional_context

        nested_initial_user_message = _json_field(
            hook_specific_output,
            "initialUserMessage",
            "initial_user_message",
        )
        if isinstance(nested_initial_user_message, str):
            output.initial_user_message = nested_initial_user_message

        nested_watch_paths = _json_field(
            hook_specific_output,
            "watchPaths",
            "watch_paths",
        )
        if isinstance(nested_watch_paths, list):
            output.watch_paths = [str(path) for path in nested_watch_paths]

        nested_permission_request = _json_field(
            hook_specific_output,
            "decision",
            "permissionRequestResult",
            "permission_request_result",
        )
        if isinstance(nested_permission_request, dict):
            output.permission_request_result = nested_permission_request
            behavior = nested_permission_request.get("behavior")
            if isinstance(behavior, str):
                output.permission_behavior = behavior
            nested_req_input = nested_permission_request.get("updatedInput")
            if isinstance(nested_req_input, dict):
                output.updated_input = nested_req_input

        updated_mcp_tool_output = _json_field(
            hook_specific_output,
            "updatedMCPToolOutput",
            "updated_mcp_tool_output",
        )
        if updated_mcp_tool_output is not None:
            output.updated_mcp_tool_output = updated_mcp_tool_output

    updated_mcp_tool_output = _json_field(
        parsed,
        "updatedMCPToolOutput",
        "updated_mcp_tool_output",
    )
    if updated_mcp_tool_output is not None:
        output.updated_mcp_tool_output = updated_mcp_tool_output

    if output.decision == "block" or output.permission_behavior == "deny":
        output.blocking = True
    return output


# ── SSRF guard for HTTP hooks ────────────────────────────────────────

def _is_blocked_v4(address: ipaddress.IPv4Address) -> bool:
    first = int(str(address).split(".")[0])
    second = int(str(address).split(".")[1])

    if first == 127:
        return False
    if first == 0:
        return True
    if first == 10:
        return True
    if first == 169 and second == 254:
        return True
    if first == 172 and 16 <= second <= 31:
        return True
    if first == 100 and 64 <= second <= 127:
        return True
    if first == 192 and second == 168:
        return True
    return False


def _mapped_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    return address.ipv4_mapped


def _is_blocked_v6(address: ipaddress.IPv6Address) -> bool:
    if address == ipaddress.IPv6Address("::1"):
        return False
    if address == ipaddress.IPv6Address("::"):
        return True
    mapped = _mapped_ipv4(address)
    if mapped is not None:
        return _is_blocked_v4(mapped)
    if address in ipaddress.ip_network("fc00::/7"):
        return True
    if address in ipaddress.ip_network("fe80::/10"):
        return True
    return False


def is_ssrf_safe(hostname: str) -> bool:
    """Check if a hostname resolves only to routable or loopback addresses."""
    import socket

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False

    for info in infos:
        raw = info[4][0]
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if isinstance(addr, ipaddress.IPv4Address):
            blocked = _is_blocked_v4(addr)
        else:
            blocked = _is_blocked_v6(addr)
        if blocked:
            logger.warning("SSRF blocked: %s -> %s", hostname, raw)
            return False
    return True


# ── Hook configuration loading ───────────────────────────────────────

_CONFIG_FILENAMES = [
    "hooks.json",
    ".ccmini/hooks.json",
]


def _find_config_paths(cwd: str | None = None) -> list[Path]:
    """Discover hook config files from user home and project directories."""
    paths: list[Path] = []

    user_config = mini_agent_path("hooks.json")
    if user_config.exists():
        paths.append(user_config)

    if cwd:
        for name in _CONFIG_FILENAMES:
            p = Path(cwd) / name
            if p.exists() and p not in paths:
                paths.append(p)

    return paths


def load_hook_config(cwd: str | None = None) -> dict[str, list[HookMatcher]]:
    """Load and merge hook configurations from all discovered config files.

    Returns ``{event_name: [HookMatcher, ...]}``.
    """
    merged: dict[str, list[HookMatcher]] = {}

    for path in _find_config_paths(cwd):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            hooks_section = data.get("hooks", {})
            for event_name, matchers_data in hooks_section.items():
                if event_name not in merged:
                    merged[event_name] = []
                for m in matchers_data:
                    specs = []
                    for h in m.get("hooks", []):
                        ht = HookType(h.get("type", "command"))
                        specs.append(HookSpec(
                            type=ht,
                            command=h.get("command", ""),
                            url=h.get("url", ""),
                            timeout=h.get("timeout", 600.0),
                            is_async=h.get("async", False),
                            headers=h.get("headers", {}),
                        ))
                    merged[event_name].append(HookMatcher(
                        matcher=m.get("matcher", "*"),
                        hooks=specs,
                    ))
        except Exception:
            logger.warning("Failed to load hook config from %s", path, exc_info=True)

    return merged


# ── Hook execution ───────────────────────────────────────────────────


async def _exec_command_hook(
    spec: HookSpec,
    hook_input: HookInput,
) -> HookOutput:
    """Execute a shell command hook with stdin JSON piping."""
    env = {
        **os.environ,
        "CCMINI_PROJECT_DIR": hook_input.cwd,
        "CCMINI_SESSION_ID": hook_input.session_id,
        "CCMINI_HOOK_EVENT": hook_input.hook_event_name,
    }

    input_json = hook_input.to_json()

    try:
        proc = await asyncio.create_subprocess_shell(
            spec.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=hook_input.cwd or None,
            env=env,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(input_json.encode("utf-8")),
            timeout=spec.timeout,
        )
        exit_code = proc.returncode or 0
    except asyncio.TimeoutError:
        logger.warning("Hook command timed out after %.0fs: %s", spec.timeout, spec.command)
        return HookOutput(exit_code=-1, stderr="Hook timed out")
    except Exception as exc:
        logger.warning("Hook command failed: %s — %s", spec.command, exc)
        return HookOutput(exit_code=-1, stderr=str(exc))

    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

    output = HookOutput(exit_code=exit_code, stdout=stdout, stderr=stderr)

    if exit_code == 2:
        output.blocking = True
        output.decision = "block"
        output.reason = stderr or "Blocked by hook"
        output.should_continue = False

    if stdout.startswith("{"):
        try:
            parsed = json.loads(stdout)
            output = _apply_hook_json(output, parsed)
        except json.JSONDecodeError:
            pass

    return output


async def _exec_http_hook(
    spec: HookSpec,
    hook_input: HookInput,
) -> HookOutput:
    """Execute an HTTP POST hook with SSRF protection."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(spec.url)
        hostname = parsed.hostname or ""

        if not is_ssrf_safe(hostname):
            return HookOutput(
                exit_code=-1,
                stderr=f"SSRF blocked: {hostname}",
                blocking=False,
            )

        import aiohttp
        headers = {**spec.headers, "Content-Type": "application/json"}
        # Interpolate env vars in header values
        for k, v in headers.items():
            headers[k] = re.sub(
                r"\$\{?(\w+)\}?",
                lambda m: os.environ.get(m.group(1), m.group(0)),
                v,
            )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                spec.url,
                data=hook_input.to_json(),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=spec.timeout),
            ) as resp:
                body = await resp.text()
                exit_code = 0 if resp.status < 400 else resp.status

                output = HookOutput(exit_code=exit_code, stdout=body)
                if body.startswith("{"):
                    try:
                        parsed_body = json.loads(body)
                        output = _apply_hook_json(output, parsed_body)
                    except json.JSONDecodeError:
                        pass
                return output

    except ImportError:
        logger.debug("aiohttp not available, skipping HTTP hook")
        return HookOutput(exit_code=-1, stderr="aiohttp not installed")
    except Exception as exc:
        logger.warning("HTTP hook failed: %s — %s", spec.url, exc)
        return HookOutput(exit_code=-1, stderr=str(exc))


async def execute_hook(
    spec: HookSpec,
    hook_input: HookInput,
) -> HookOutput:
    """Execute a single hook spec."""
    if spec.type == HookType.COMMAND:
        return await _exec_command_hook(spec, hook_input)
    elif spec.type == HookType.HTTP:
        return await _exec_http_hook(spec, hook_input)
    return HookOutput(exit_code=-1, stderr=f"Unknown hook type: {spec.type}")


# ── Hook dispatcher ──────────────────────────────────────────────────


class UserHookRunner:
    """Loads user hook configs and dispatches events to matching hooks.

    Usage::

        runner = UserHookRunner(cwd="/path/to/project")
        result = await runner.fire(
            HookEvent.PRE_TOOL_USE,
            HookInput(tool_name="Bash", tool_input={"command": "rm -rf /"}),
        )
        if result.blocking:
            # Tool call should be blocked
            print(result.reason)
    """

    def __init__(self, cwd: str | None = None, session_id: str = "") -> None:
        self._cwd = cwd or os.getcwd()
        self._session_id = session_id
        self._config = load_hook_config(cwd)
        self._disabled = False

    @property
    def disabled(self) -> bool:
        return self._disabled

    @disabled.setter
    def disabled(self, value: bool) -> None:
        self._disabled = value

    def reload(self) -> None:
        """Reload hook configuration from disk."""
        self._config = load_hook_config(self._cwd)

    def has_hooks(self, event: HookEvent) -> bool:
        return event.value in self._config and len(self._config[event.value]) > 0

    async def fire(
        self,
        event: HookEvent,
        hook_input: HookInput | None = None,
        *,
        tool_name: str = "",
    ) -> HookOutput:
        """Fire all matching hooks for an event.

        Returns a merged ``HookOutput``. If any hook blocks, the
        merged result will have ``blocking=True``.
        """
        if self._disabled:
            return HookOutput()

        matchers = self._config.get(event.value, [])
        if not matchers:
            return HookOutput()

        if hook_input is None:
            hook_input = HookInput()
        hook_input.cwd = hook_input.cwd or self._cwd
        hook_input.session_id = hook_input.session_id or self._session_id
        if not hook_input.transcript_path and hook_input.session_id:
            hook_input.transcript_path = str(mini_agent_path("sessions", f"{hook_input.session_id}.jsonl"))
        hook_input.hook_event_name = event.value

        merged = HookOutput()

        for matcher in matchers:
            if tool_name and not matcher.matches(tool_name):
                continue

            for spec in matcher.hooks:
                if spec.is_async:
                    asyncio.create_task(_fire_async(spec, hook_input))
                    continue

                output = await execute_hook(spec, hook_input)

                if output.blocking:
                    merged.blocking = True
                    merged.decision = output.decision or merged.decision
                    merged.reason = output.reason or merged.reason
                    merged.should_continue = False

                if output.system_message:
                    merged.system_message = (
                        f"{merged.system_message}\n{output.system_message}".strip()
                    )

                if output.additional_context:
                    merged.additional_context = (
                        f"{merged.additional_context}\n{output.additional_context}".strip()
                    )

                if output.raw_json:
                    merged.raw_json.update(output.raw_json)

                if output.updated_input is not None:
                    merged.updated_input = output.updated_input

                if output.updated_mcp_tool_output is not None:
                    merged.updated_mcp_tool_output = output.updated_mcp_tool_output

                if output.permission_behavior:
                    merged.permission_behavior = output.permission_behavior

                if output.permission_request_result is not None:
                    merged.permission_request_result = output.permission_request_result

                if output.retry is not None:
                    merged.retry = output.retry

                if output.watch_paths is not None:
                    merged.watch_paths = list(output.watch_paths)

                if output.initial_user_message:
                    merged.initial_user_message = output.initial_user_message

                if output.stop_reason:
                    merged.stop_reason = output.stop_reason

                if output.hook_specific_output is not None:
                    merged.hook_specific_output = dict(output.hook_specific_output)

                if not output.should_continue:
                    merged.should_continue = False

        return merged


async def _fire_async(spec: HookSpec, hook_input: HookInput) -> None:
    """Fire an async hook in the background."""
    try:
        await execute_hook(spec, hook_input)
    except Exception:
        logger.debug("Async hook failed", exc_info=True)


# ── Script discovery ─────────────────────────────────────────────────

_HOOKS_DIR = mini_agent_path("hooks")

_NAMING_PREFIXES = {
    "pre_tool_": HookEvent.PRE_TOOL_USE,
    "post_tool_": HookEvent.POST_TOOL_USE,
    "on_event_": HookEvent.NOTIFICATION,
    "pre_query_": HookEvent.SESSION_START,  # maps to a session-level event
    "post_query_": HookEvent.SESSION_END,
    "session_start_": HookEvent.SESSION_START,
    "session_end_": HookEvent.SESSION_END,
    "pre_compact_": HookEvent.PRE_COMPACT,
    "post_compact_": HookEvent.POST_COMPACT,
    "stop_": HookEvent.STOP,
    "setup_": HookEvent.SETUP,
    "subagent_start_": HookEvent.SUBAGENT_START,
    "subagent_stop_": HookEvent.SUBAGENT_STOP,
}


@dataclass(slots=True)
class DiscoveredScript:
    """A Python hook script found on disk."""
    path: Path
    event: HookEvent
    name: str
    mtime: float = 0.0


def discover_user_hooks(
    hooks_dir: Path | None = None,
) -> list[DiscoveredScript]:
    """Scan ``~/.ccmini/hooks/`` for Python scripts.

    Recognises filenames like ``pre_tool_guard.py``, ``post_tool_log.py``,
    ``on_event_notify.py``.  Returns a list of :class:`DiscoveredScript`
    sorted by filename.
    """
    directory = hooks_dir or _HOOKS_DIR
    if not directory.is_dir():
        return []

    discovered: list[DiscoveredScript] = []
    for py_file in sorted(directory.glob("*.py")):
        stem = py_file.stem
        matched_event: HookEvent | None = None
        for prefix, event in _NAMING_PREFIXES.items():
            if stem.startswith(prefix):
                matched_event = event
                break

        if matched_event is None:
            continue

        try:
            mtime = py_file.stat().st_mtime
        except OSError:
            mtime = 0.0

        discovered.append(DiscoveredScript(
            path=py_file,
            event=matched_event,
            name=stem,
            mtime=mtime,
        ))

    return discovered


# ── Script sandbox ──────────────────────────────────────────────────

_SAFE_BUILTINS = {
    "abs", "all", "any", "bool", "bytes", "callable", "chr", "dict",
    "dir", "divmod", "enumerate", "filter", "float", "format",
    "frozenset", "getattr", "hasattr", "hash", "hex", "id", "int",
    "isinstance", "issubclass", "iter", "len", "list", "map", "max",
    "min", "next", "oct", "ord", "pow", "print", "range", "repr",
    "reversed", "round", "set", "slice", "sorted", "str", "sum",
    "tuple", "type", "vars", "zip",
}

_ALLOWED_IMPORTS = frozenset({
    "json", "re", "math", "datetime", "hashlib", "base64",
    "collections", "itertools", "functools", "operator",
    "textwrap", "string", "enum", "dataclasses", "typing",
    "pathlib", "urllib.parse", "copy", "uuid",
})


class ScriptSandbox:
    """Execute user hook scripts in a restricted environment.

    Limits available builtins, restricts ``__import__`` to a safe set,
    and enforces a per-script timeout (default 5 s).
    """

    def __init__(
        self,
        *,
        timeout: float = 5.0,
        allowed_imports: frozenset[str] | None = None,
        project_dir: str | Path | None = None,
    ) -> None:
        self._timeout = timeout
        self._allowed_imports = allowed_imports or _ALLOWED_IMPORTS
        self._project_dir = str(Path(project_dir).resolve()) if project_dir else None

    def _make_globals(self) -> dict[str, object]:
        import builtins as _builtins

        safe_bi: dict[str, object] = {
            k: getattr(_builtins, k) for k in _SAFE_BUILTINS if hasattr(_builtins, k)
        }
        safe_bi["__import__"] = self._restricted_import
        safe_bi["__name__"] = "__sandbox__"
        return {"__builtins__": safe_bi}

    def _restricted_import(
        self, name: str, *args: object, **kwargs: object,
    ) -> object:
        top_level = name.split(".")[0]
        if top_level not in self._allowed_imports:
            raise ImportError(
                f"Import of '{name}' is not allowed in hook scripts. "
                f"Allowed: {', '.join(sorted(self._allowed_imports))}"
            )
        return __import__(name, *args, **kwargs)

    async def execute_script(
        self,
        script_path: Path,
        *,
        hook_input: HookInput | None = None,
    ) -> HookOutput:
        """Run a Python script in the sandbox with a timeout."""
        try:
            source = script_path.read_text(encoding="utf-8")
        except Exception as exc:
            return HookOutput(exit_code=-1, stderr=f"Cannot read script: {exc}")

        sandbox_globals = self._make_globals()
        if hook_input is not None:
            sandbox_globals["hook_input"] = hook_input
            sandbox_globals["input_data"] = json.loads(hook_input.to_json())

        loop = asyncio.get_running_loop()

        def _run() -> HookOutput:
            try:
                exec(compile(source, str(script_path), "exec"), sandbox_globals)  # noqa: S102
                result = sandbox_globals.get("result")
                if isinstance(result, dict):
                    output = HookOutput(
                        exit_code=0,
                        stdout=json.dumps(result, ensure_ascii=False),
                    )
                    return _apply_hook_json(output, result)
                return HookOutput(exit_code=0, stdout=str(result) if result else "")
            except Exception as exc:
                return HookOutput(exit_code=1, stderr=str(exc))

        try:
            output = await asyncio.wait_for(
                loop.run_in_executor(None, _run),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            output = HookOutput(
                exit_code=-1,
                stderr=f"Script timed out after {self._timeout}s",
            )

        return output


# ── Script hot reload ───────────────────────────────────────────────


class ScriptHotReloader:
    """Watch the hooks directory for changes and reload scripts.

    Uses polling (checks mtimes every *poll_interval* seconds) to stay
    cross-platform without extra dependencies.
    """

    def __init__(
        self,
        hooks_dir: Path | None = None,
        *,
        poll_interval: float = 2.0,
        on_reload: "Callable[[list[DiscoveredScript]], None] | None" = None,
    ) -> None:
        self._hooks_dir = hooks_dir or _HOOKS_DIR
        self._poll_interval = poll_interval
        self._on_reload = on_reload
        self._known_mtimes: dict[str, float] = {}
        self._task: asyncio.Task[None] | None = None
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable_hot_reload(self) -> None:
        """Start watching for changes."""
        if self._enabled:
            return
        self._enabled = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.debug("Hook hot-reload enabled for %s", self._hooks_dir)

    def disable_hot_reload(self) -> None:
        """Stop watching."""
        self._enabled = False
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

    async def _poll_loop(self) -> None:
        try:
            while self._enabled:
                self._check_changes()
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            pass

    def _check_changes(self) -> None:
        current_scripts = discover_user_hooks(self._hooks_dir)
        changed = False

        current_paths: dict[str, float] = {}
        for script in current_scripts:
            key = str(script.path)
            current_paths[key] = script.mtime
            prev = self._known_mtimes.get(key)
            if prev is None or prev != script.mtime:
                changed = True

        if set(current_paths.keys()) != set(self._known_mtimes.keys()):
            changed = True

        self._known_mtimes = current_paths

        if changed and self._on_reload is not None:
            try:
                self._on_reload(current_scripts)
            except Exception:
                logger.debug("Hot-reload callback error", exc_info=True)
