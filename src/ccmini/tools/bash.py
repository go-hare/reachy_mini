"""BashTool — execute shell commands with streaming output.

This is the first built-in tool that uses ``stream_execute`` to yield
``ToolProgress`` events as stdout lines arrive, giving the user
real-time visibility into command execution.

On Windows, uses PowerShell; on Unix, uses bash.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from collections.abc import AsyncIterator
from typing import Any

from ..tool import Tool, ToolProgress, ToolUseContext, get_working_directory

_DEFAULT_TIMEOUT = 120
_MAX_OUTPUT = 256_000

# Timeout for build/install commands (detected by _detect_long_command)
_LONG_COMMAND_TIMEOUT = 300

# Maximum output characters before middle-truncation kicks in
_MAX_OUTPUT_CHARS = 100_000


class BashTool(Tool):
    name = "Bash"
    description = (
        "Execute a shell command and return its output. "
        "Commands run in a subprocess with a timeout. "
        "Supports streaming output for long-running commands."
    )
    instructions = """\
Executes a shell command and returns its output.

IMPORTANT: Avoid using this tool to run cat, head, tail, sed, awk, find, \
or grep commands unless explicitly instructed. Instead, use the appropriate \
dedicated tool — this provides a better experience for the user:
 - File search: Use Glob (NOT find or ls)
 - Content search: Use Grep (NOT grep or rg in shell)
 - Read files: Use Read (NOT cat/head/tail)
 - Edit files: Use Edit (NOT sed/awk)
 - Write files: Use Write (NOT echo >/cat <<EOF)
 - Communication: Output text directly (NOT echo/printf)

Reserve bash exclusively for system commands and terminal operations \
that require shell execution.

## Instructions

- If your command will create new files or directories, first run \
`ls` to verify the parent directory exists and is the correct location.
- Always quote file paths containing spaces with double quotes.
- Try to maintain your current working directory throughout the session \
by using absolute paths and avoiding `cd`.
- When issuing multiple commands:
  - If independent and can run in parallel, make multiple bash tool \
calls in a single message.
  - If dependent and must run sequentially, use `&&` to chain them.
  - Use `;` only when order matters but failure of earlier commands is OK.
  - Do NOT use newlines to separate commands.
- You can use the `background` parameter to run the command in the \
background. Only use this if you don't need the result immediately. \
You do not need to use '&' at the end of the command.
- You can use the `working_directory` parameter to run commands in a \
different directory without using `cd`.

## Git commands

- Prefer creating a new commit rather than amending an existing commit.
- Before running destructive operations (git reset --hard, git push --force, \
git checkout --), consider safer alternatives. Only use destructive \
operations when they are truly the best approach.
- Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign) unless \
the user has explicitly asked for it. If a hook fails, investigate and fix \
the underlying issue.
- Never use git commands with the -i flag (interactive input not supported).\
"""
    is_read_only = False
    supports_streaming = True

    def __init__(
        self,
        *,
        timeout: int = _DEFAULT_TIMEOUT,
        working_dir: str | None = None,
        allowed_commands: list[str] | None = None,
    ) -> None:
        self._timeout = timeout
        self._working_dir = working_dir
        self._allowed_commands = allowed_commands
        self._cwd: str | None = working_dir
        self._background_processes: dict[int, asyncio.subprocess.Process] = {}

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {
                    "type": "integer",
                    "description": f"Timeout in seconds (default: {_DEFAULT_TIMEOUT})",
                },
                "background": {
                    "type": "boolean",
                    "description": "Run command in background and return immediately with PID (default: false)",
                },
                "working_directory": {
                    "type": "string",
                    "description": "Working directory to execute the command in (overrides current cwd)",
                },
            },
            "required": ["command"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        """Non-streaming execution — collects all output and returns."""
        command: str = kwargs["command"]
        timeout = kwargs.get("timeout") or self._resolve_timeout(command)
        background: bool = kwargs.get("background", False) or is_background_command(command)
        working_dir: str | None = kwargs.get("working_directory") or get_working_directory(context) or None

        if err := self._check_allowed(command):
            return err

        if err := self._check_command_safety(command):
            return err

        effective_cwd = working_dir or self._cwd or self._working_dir

        if background:
            return await self._run_background(command, effective_cwd)

        try:
            proc = await self._create_process(command, cwd=effective_cwd)
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return f"Error: Command timed out after {timeout}s"
        except Exception as exc:
            return f"Error running command: {exc}"

        self._track_cd(command, effective_cwd)

        raw_stdout = stdout.decode("utf-8", errors="replace")
        raw_stderr = stderr.decode("utf-8", errors="replace")
        raw_stdout = _truncate_output(raw_stdout, _MAX_OUTPUT_CHARS)
        raw_stderr = _truncate_output(raw_stderr, _MAX_OUTPUT_CHARS)

        return _format_output(raw_stdout, raw_stderr, proc.returncode or 0)

    async def stream_execute(
        self, *, context: ToolUseContext, **kwargs: Any
    ) -> AsyncIterator[ToolProgress | str]:
        """Streaming execution — yields stdout lines as ToolProgress."""
        command: str = kwargs["command"]
        timeout = kwargs.get("timeout") or self._resolve_timeout(command)
        background: bool = kwargs.get("background", False) or is_background_command(command)
        working_dir: str | None = kwargs.get("working_directory") or get_working_directory(context) or None

        if err := self._check_allowed(command):
            yield err
            return

        if err := self._check_command_safety(command):
            yield err
            return

        effective_cwd = working_dir or self._cwd or self._working_dir

        if background:
            yield await self._run_background(command, effective_cwd)
            return

        try:
            proc = await self._create_process(command, cwd=effective_cwd)
        except Exception as exc:
            yield f"Error running command: {exc}"
            return

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        total_size = 0
        timed_out = False

        async def _read_stderr() -> None:
            assert proc.stderr is not None
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                stderr_lines.append(line.decode("utf-8", errors="replace"))

        stderr_task = asyncio.create_task(_read_stderr())

        try:
            assert proc.stdout is not None
            while True:
                try:
                    line_bytes = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    break

                if not line_bytes:
                    break

                line = line_bytes.decode("utf-8", errors="replace").rstrip("\n\r")
                stdout_lines.append(line)
                total_size += len(line)

                yield ToolProgress(content=line)

                if total_size > _MAX_OUTPUT:
                    yield ToolProgress(content="... (output truncated)")
                    break

            await asyncio.wait_for(stderr_task, timeout=5.0)
        except asyncio.TimeoutError:
            timed_out = True

        if timed_out:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass

        self._track_cd(command, effective_cwd)

        rc = proc.returncode or 0
        stdout_text = "\n".join(stdout_lines)
        stderr_text = "".join(stderr_lines)

        stdout_text = _truncate_output(stdout_text, _MAX_OUTPUT_CHARS)
        stderr_text = _truncate_output(stderr_text, _MAX_OUTPUT_CHARS)

        if timed_out:
            yield _format_output(stdout_text, stderr_text, rc) + f"\n(timed out after {timeout}s)"
        else:
            yield _format_output(stdout_text, stderr_text, rc)

    async def _create_process(
        self, command: str, *, cwd: str | None = None,
    ) -> asyncio.subprocess.Process:
        effective_cwd = cwd or self._cwd or self._working_dir
        if sys.platform == "win32":
            return await asyncio.create_subprocess_exec(
                "powershell", "-NoProfile", "-Command", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=effective_cwd,
            )
        else:
            return await asyncio.create_subprocess_exec(
                "bash", "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=effective_cwd,
            )

    def _check_allowed(self, command: str) -> str | None:
        if self._allowed_commands is None:
            return None
        cmd_name = command.strip().split()[0] if command.strip() else ""
        if cmd_name not in self._allowed_commands:
            return f"Error: Command '{cmd_name}' is not in the allowed list."
        return None

    def _check_command_safety(self, command: str) -> str | None:
        """Check command against BashCommandAnalyzer before execution."""
        try:
            from ..permissions import BashCommandAnalyzer, RiskLevel
        except ImportError:
            return None

        risk, reason = BashCommandAnalyzer.classify(command)

        if risk == RiskLevel.BLOCKED:
            return f"Error: Command blocked — {reason}"
        if risk == RiskLevel.DANGEROUS:
            return f"Error: Command rejected (dangerous) — {reason}"
        return None

    def _get_command_warning(self, command: str) -> str | None:
        """Return a warning string if the command needs review (not fatal)."""
        try:
            from ..permissions import BashCommandAnalyzer, RiskLevel
        except ImportError:
            return None

        risk, reason = BashCommandAnalyzer.classify(command)
        if risk == RiskLevel.NEEDS_REVIEW:
            return f"Warning: {reason}"
        return None

    def _resolve_timeout(self, command: str) -> int:
        """Pick the appropriate timeout based on command type."""
        if detect_long_command(command):
            return _LONG_COMMAND_TIMEOUT
        return self._timeout

    def _track_cd(self, command: str, effective_cwd: str | None) -> None:
        """Track ``cd`` commands and update internal working directory."""
        new_dir = _extract_cd_target(command, effective_cwd)
        if new_dir is not None:
            self._cwd = new_dir

    async def _run_background(self, command: str, cwd: str | None) -> str:
        """Run command in background, return immediately with PID."""
        cmd = command.rstrip().rstrip("&").rstrip()
        try:
            proc = await self._create_process(cmd, cwd=cwd)
        except Exception as exc:
            return f"Error starting background command: {exc}"

        pid = proc.pid
        if pid is not None:
            self._background_processes[pid] = proc
        return f"Background process started with PID {pid}"

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def get_current_working_dir(self) -> str | None:
        """Return the tracked current working directory."""
        return self._cwd or self._working_dir

    def get_background_processes(self) -> dict[int, asyncio.subprocess.Process]:
        """Return a snapshot of tracked background processes."""
        return dict(self._background_processes)


def _format_output(stdout: str, stderr: str, returncode: int) -> str:
    parts: list[str] = []
    if stdout.strip():
        parts.append(stdout.strip())
    if stderr.strip():
        parts.append(f"STDERR:\n{stderr.strip()}")
    if returncode != 0:
        parts.append(f"Exit code: {returncode}")
    return "\n".join(parts) if parts else "(no output)"


# -----------------------------------------------------------------------
# Feature: Output size limits (middle-truncation)
# -----------------------------------------------------------------------

def _truncate_output(text: str, max_chars: int = _MAX_OUTPUT_CHARS) -> str:
    """Truncate output from the middle if it exceeds *max_chars*.

    Keeps the first and last quarter of the allowed size so both the
    beginning and tail of the output remain visible.
    """
    if len(text) <= max_chars:
        return text
    keep = max_chars // 2
    head = text[:keep]
    tail = text[-keep:]
    removed = len(text) - max_chars
    return f"{head}\n\n[...truncated {removed} chars...]\n\n{tail}"


# -----------------------------------------------------------------------
# Feature: Timeout — detect long-running commands
# -----------------------------------------------------------------------

_LONG_COMMAND_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(npm\s+install|npm\s+ci|yarn\s+install|pnpm\s+install)\b", re.I),
    re.compile(r"\b(pip\s+install|pip3\s+install|poetry\s+install|pdm\s+install)\b", re.I),
    re.compile(r"\b(cargo\s+build|cargo\s+install)\b", re.I),
    re.compile(r"\b(make|cmake|ninja|gradle|mvn|ant)\b", re.I),
    re.compile(r"\b(docker\s+build|docker\s+compose\s+build)\b", re.I),
    re.compile(r"\b(go\s+build|go\s+install)\b", re.I),
    re.compile(r"\b(apt-get\s+install|apt\s+install|yum\s+install|brew\s+install)\b", re.I),
    re.compile(r"\b(dotnet\s+build|dotnet\s+restore)\b", re.I),
]


def detect_long_command(command: str) -> bool:
    """Return True if *command* looks like a build or install invocation."""
    for pat in _LONG_COMMAND_PATTERNS:
        if pat.search(command):
            return True
    return False


# -----------------------------------------------------------------------
# Feature: Background execution helpers
# -----------------------------------------------------------------------

def is_background_command(command: str) -> bool:
    """Return True if the command ends with ``&`` (background request)."""
    stripped = command.rstrip()
    return stripped.endswith("&") and not stripped.endswith("&&")


# -----------------------------------------------------------------------
# Feature: Working directory tracking
# -----------------------------------------------------------------------

_CD_RE = re.compile(
    r"""(?:^|&&|;)\s*cd\s+("(?P<dq>[^"]+)"|'(?P<sq>[^']+)'|(?P<bare>\S+))""",
)


def _extract_cd_target(command: str, cwd: str | None) -> str | None:
    """Extract the *last* ``cd`` target from a compound command string.

    Returns the resolved absolute path, or *None* when no ``cd`` is found.
    """
    last_target: str | None = None
    for m in _CD_RE.finditer(command):
        target = m.group("dq") or m.group("sq") or m.group("bare") or ""
        if target:
            last_target = target

    if last_target is None:
        return None

    last_target = os.path.expanduser(last_target)
    if not os.path.isabs(last_target):
        base = cwd or os.getcwd()
        last_target = os.path.normpath(os.path.join(base, last_target))
    return last_target
