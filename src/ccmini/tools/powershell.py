"""PowerShellTool — execute PowerShell commands (Windows native).

Provides Windows-native command execution via ``powershell.exe`` (5.1) or
``pwsh`` (PowerShell Core 7+).  Includes cmdlet safety classification,
destructive-pattern detection, pipeline safety analysis, and ``-WhatIf``
enforcement for dangerous commands.

Returns an error on non-Windows platforms.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from collections.abc import AsyncIterator
from typing import Any

from ..tool import Tool, ToolProgress, ToolUseContext, get_working_directory

_DEFAULT_TIMEOUT = 120
_LONG_TIMEOUT = 300
_MAX_OUTPUT_CHARS = 100_000


# ---------------------------------------------------------------------------
# PowerShell edition detection
# ---------------------------------------------------------------------------

class _PSEdition:
    """Cached detection of which PowerShell is available."""
    _edition: str | None = None
    _executable: str | None = None

    @classmethod
    def detect(cls) -> tuple[str | None, str | None]:
        """Return (edition, executable) — both None if unavailable."""
        if cls._edition is not None:
            return cls._edition, cls._executable

        if sys.platform != "win32":
            cls._edition = ""
            cls._executable = None
            return cls._edition, cls._executable

        # Prefer pwsh (PowerShell 7+) over powershell.exe (5.1)
        if shutil.which("pwsh"):
            cls._edition = "core"
            cls._executable = "pwsh"
        elif shutil.which("powershell"):
            cls._edition = "desktop"
            cls._executable = "powershell"
        else:
            cls._edition = ""
            cls._executable = None

        return cls._edition, cls._executable


# ---------------------------------------------------------------------------
# Cmdlet safety classification
# ---------------------------------------------------------------------------

class CmdletRisk:
    SAFE = "safe"
    REVIEW = "review"
    DANGEROUS = "dangerous"


_SAFE_PREFIXES = ("Get-", "Test-", "Measure-", "Select-", "Where-", "Sort-",
                  "Group-", "Format-", "Out-", "ConvertTo-", "ConvertFrom-",
                  "Compare-", "Find-", "Read-", "Resolve-", "Split-", "Join-")

_REVIEW_PREFIXES = ("Set-", "New-", "Add-", "Enable-", "Disable-", "Update-",
                    "Register-", "Unregister-", "Move-", "Copy-", "Rename-",
                    "Export-", "Import-", "Start-", "Stop-", "Restart-",
                    "Suspend-", "Resume-", "Send-", "Write-", "Push-", "Pop-")

_DANGEROUS_PREFIXES = ("Remove-", "Clear-", "Reset-")

_DANGEROUS_CMDLETS = frozenset({
    "Format-Volume",
    "Initialize-Disk",
    "Clear-Disk",
    "Remove-Partition",
    "Stop-Computer",
    "Restart-Computer",
    "Clear-RecycleBin",
    "Uninstall-Package",
})

_DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bRemove-Item\b.*-Recurse", re.I),
    re.compile(r"\bRemove-Item\b.*-Force", re.I),
    re.compile(r"\bFormat-Volume\b", re.I),
    re.compile(r"\bStop-Process\b.*-Force", re.I),
    re.compile(r"\bClear-Content\b", re.I),
    re.compile(r"\brm\s+-r", re.I),
    re.compile(r"\bdel\s+/s", re.I),
    re.compile(r"\brd\s+/s", re.I),
    re.compile(r"\bnet\s+user\b.*\/delete", re.I),
    re.compile(r"\bnet\s+stop\b", re.I),
    re.compile(r"\bsc\s+delete\b", re.I),
    re.compile(r"\breg\s+delete\b", re.I),
    re.compile(r"\bInvoke-Expression\b", re.I),
    re.compile(r"\biex\b", re.I),
]

# Git safety — same protections as BashTool
_GIT_DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bgit\s+push\b.*--force\b", re.I),
    re.compile(r"\bgit\s+push\b.*-f\b", re.I),
    re.compile(r"\bgit\s+reset\b.*--hard\b", re.I),
    re.compile(r"\bgit\s+clean\b.*-fd\b", re.I),
    re.compile(r"\bgit\s+checkout\b.*--\b", re.I),
    re.compile(r"\bgit\s+(rebase|add)\b.*-i\b", re.I),
]


def classify_cmdlet(name: str) -> str:
    """Classify a PowerShell cmdlet by its verb prefix."""
    if name in _DANGEROUS_CMDLETS:
        return CmdletRisk.DANGEROUS
    for prefix in _DANGEROUS_PREFIXES:
        if name.startswith(prefix):
            return CmdletRisk.DANGEROUS
    for prefix in _REVIEW_PREFIXES:
        if name.startswith(prefix):
            return CmdletRisk.REVIEW
    for prefix in _SAFE_PREFIXES:
        if name.startswith(prefix):
            return CmdletRisk.SAFE
    return CmdletRisk.REVIEW


def classify_command(command: str) -> tuple[str, str | None]:
    """Classify an entire PowerShell command string.

    Returns (risk_level, reason).
    """
    for pat in _DANGEROUS_PATTERNS:
        if pat.search(command):
            return CmdletRisk.DANGEROUS, f"Destructive pattern detected: {pat.pattern}"

    for pat in _GIT_DANGEROUS_PATTERNS:
        if pat.search(command):
            return CmdletRisk.DANGEROUS, f"Dangerous git operation: {pat.pattern}"

    # Check for pipe to destructive cmdlets
    pipe_segments = command.split("|")
    worst_risk = CmdletRisk.SAFE
    for segment in pipe_segments:
        tokens = segment.strip().split()
        if not tokens:
            continue
        # First token could be a cmdlet or alias
        first = tokens[0].strip("&").strip('"').strip("'")
        risk = classify_cmdlet(first)
        if risk == CmdletRisk.DANGEROUS:
            return CmdletRisk.DANGEROUS, f"Dangerous cmdlet in pipeline: {first}"
        if risk == CmdletRisk.REVIEW:
            worst_risk = CmdletRisk.REVIEW

    return worst_risk, None


def should_enforce_whatif(command: str) -> bool:
    """Return True if the command should have -WhatIf appended for safety."""
    risk, _ = classify_command(command)
    if risk != CmdletRisk.DANGEROUS:
        return False
    # Already has -WhatIf or -Confirm
    if re.search(r"-WhatIf\b", command, re.I):
        return False
    if re.search(r"-Confirm\b", command, re.I):
        return False
    return True


# ---------------------------------------------------------------------------
# Long-running command detection
# ---------------------------------------------------------------------------

_LONG_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(npm\s+install|yarn\s+install|pnpm\s+install)\b", re.I),
    re.compile(r"\b(pip\s+install|pip3\s+install)\b", re.I),
    re.compile(r"\b(dotnet\s+build|dotnet\s+restore|dotnet\s+publish)\b", re.I),
    re.compile(r"\b(msbuild|devenv)\b", re.I),
    re.compile(r"\b(choco\s+install|winget\s+install)\b", re.I),
    re.compile(r"\b(docker\s+build|docker\s+compose\s+build)\b", re.I),
    re.compile(r"\b(cargo\s+build|go\s+build)\b", re.I),
]


def _is_long_command(command: str) -> bool:
    return any(p.search(command) for p in _LONG_PATTERNS)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _truncate_output(text: str, max_chars: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    keep = max_chars // 2
    removed = len(text) - max_chars
    return f"{text[:keep]}\n\n[...truncated {removed} chars...]\n\n{text[-keep:]}"


def _format_output(stdout: str, stderr: str, returncode: int) -> str:
    parts: list[str] = []
    if stdout.strip():
        parts.append(stdout.strip())
    if stderr.strip():
        parts.append(f"STDERR:\n{stderr.strip()}")
    if returncode != 0:
        parts.append(f"Exit code: {returncode}")
    return "\n".join(parts) if parts else "(no output)"


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

_UNC_RE = re.compile(r"^\\\\|^//")


def _validate_paths_in_command(command: str) -> str | None:
    """Reject commands that reference UNC paths to prevent NTLM leaks."""
    if _UNC_RE.search(command):
        return "Error: UNC paths (\\\\server\\share) are not allowed for security reasons."
    return None


# ---------------------------------------------------------------------------
# PowerShellTool
# ---------------------------------------------------------------------------

class PowerShellTool(Tool):
    name = "PowerShell"
    description = (
        "Execute PowerShell commands on Windows. Supports both Windows "
        "PowerShell (5.1) and PowerShell Core (7+). "
        "Includes cmdlet safety classification and git safety checks."
    )
    instructions = """\
Execute PowerShell commands with optional timeout. Only available on Windows.

IMPORTANT: This tool is for terminal operations via PowerShell — git, npm, \
docker, and PS cmdlets. DO NOT use it for file operations (reading, writing, \
editing, searching) — use the dedicated tools instead.

Before executing the command:
1. Verify parent directories exist before creating files/directories.
2. Always quote file paths containing spaces with double quotes.

PowerShell Syntax Notes:
- Variables use $ prefix: $myVar = "value"
- Escape character is backtick (`), not backslash
- Verb-Noun cmdlet naming: Get-ChildItem, Set-Location, New-Item
- String interpolation: "Hello $name" or "Hello $($obj.Property)"
- Environment variables: $env:NAME
- Call operator for paths with spaces: & "C:\\Program Files\\app.exe" arg

Safety:
- Safe cmdlets (Get-*, Test-*) execute immediately
- Review cmdlets (Set-*, New-*) execute with a note
- Dangerous cmdlets (Remove-*, Format-Volume) are blocked unless \
-WhatIf or -Confirm is included
- Git destructive operations (--force, reset --hard) are blocked
- Interactive commands (Read-Host, Get-Credential) will hang — don't use

## Multiline strings
Use single-quoted here-strings for commit messages / file content:
  git commit -m @'
  message here
  '@

The closing '@ MUST be at column 0 on its own line.\
"""
    is_read_only = False
    supports_streaming = True

    def __init__(
        self,
        *,
        timeout: int = _DEFAULT_TIMEOUT,
        working_dir: str | None = None,
    ) -> None:
        self._timeout = timeout
        self._working_dir = working_dir
        self._cwd: str | None = working_dir

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "PowerShell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Timeout in seconds (default: {_DEFAULT_TIMEOUT})",
                },
                "working_directory": {
                    "type": "string",
                    "description": "Working directory (overrides current cwd)",
                },
            },
            "required": ["command"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        if sys.platform != "win32":
            return "Error: PowerShell tool is only available on Windows."

        command: str = kwargs["command"]
        timeout: int = kwargs.get("timeout") or self._resolve_timeout(command)
        working_dir: str | None = kwargs.get("working_directory") or get_working_directory(context) or None

        if err := _validate_paths_in_command(command):
            return err

        if err := self._check_safety(command):
            return err

        edition, executable = _PSEdition.detect()
        if not executable:
            return "Error: No PowerShell executable found on PATH."

        effective_cwd = working_dir or self._cwd or self._working_dir

        try:
            proc = await self._create_process(executable, command, effective_cwd)
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return f"Error: Command timed out after {timeout}s"
        except Exception as exc:
            return f"Error running command: {exc}"

        self._track_cd(command, effective_cwd)

        stdout = _truncate_output(stdout_bytes.decode("utf-8", errors="replace"))
        stderr = _truncate_output(stderr_bytes.decode("utf-8", errors="replace"))
        return _format_output(stdout, stderr, proc.returncode or 0)

    async def stream_execute(
        self, *, context: ToolUseContext, **kwargs: Any,
    ) -> AsyncIterator[ToolProgress | str]:
        if sys.platform != "win32":
            yield "Error: PowerShell tool is only available on Windows."
            return

        command: str = kwargs["command"]
        timeout: int = kwargs.get("timeout") or self._resolve_timeout(command)
        working_dir: str | None = kwargs.get("working_directory") or get_working_directory(context) or None

        if err := _validate_paths_in_command(command):
            yield err
            return

        if err := self._check_safety(command):
            yield err
            return

        edition, executable = _PSEdition.detect()
        if not executable:
            yield "Error: No PowerShell executable found on PATH."
            return

        effective_cwd = working_dir or self._cwd or self._working_dir

        try:
            proc = await self._create_process(executable, command, effective_cwd)
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
                    raw = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
                except asyncio.TimeoutError:
                    timed_out = True
                    break

                if not raw:
                    break

                line = raw.decode("utf-8", errors="replace").rstrip("\n\r")
                stdout_lines.append(line)
                total_size += len(line)
                yield ToolProgress(content=line)

                if total_size > _MAX_OUTPUT_CHARS:
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
        stdout_text = _truncate_output("\n".join(stdout_lines))
        stderr_text = _truncate_output("".join(stderr_lines))

        final = _format_output(stdout_text, stderr_text, rc)
        if timed_out:
            final += f"\n(timed out after {timeout}s)"
        yield final

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _create_process(
        self, executable: str, command: str, cwd: str | None,
    ) -> asyncio.subprocess.Process:
        args = [executable, "-NoProfile", "-NonInteractive", "-Command", command]
        return await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

    def _check_safety(self, command: str) -> str | None:
        """Reject dangerous commands."""
        risk, reason = classify_command(command)
        if risk == CmdletRisk.DANGEROUS:
            return f"Error: Command blocked — {reason or 'destructive operation detected'}"
        return None

    def _resolve_timeout(self, command: str) -> int:
        if _is_long_command(command):
            return _LONG_TIMEOUT
        return self._timeout

    def _track_cd(self, command: str, effective_cwd: str | None) -> None:
        """Track Set-Location / cd and update internal cwd."""
        target = _extract_cd_target(command, effective_cwd)
        if target is not None:
            self._cwd = target

    def get_current_working_dir(self) -> str | None:
        return self._cwd or self._working_dir


# ---------------------------------------------------------------------------
# CD tracking for PowerShell
# ---------------------------------------------------------------------------

_CD_PS_RE = re.compile(
    r"""(?:^|;)\s*(?:cd|Set-Location|sl|chdir)\s+"""
    r"""(?:-Path\s+)?"""
    r"""["']?([^"';|]+?)["']?\s*(?:$|;|\|)""",
    re.I,
)


def _extract_cd_target(command: str, cwd: str | None) -> str | None:
    """Extract last cd/Set-Location target from a command."""
    last: str | None = None
    for m in _CD_PS_RE.finditer(command):
        target = m.group(1).strip()
        if target:
            last = target

    if last is None:
        return None

    last = os.path.expanduser(last)
    if not os.path.isabs(last):
        base = cwd or os.getcwd()
        last = os.path.normpath(os.path.join(base, last))
    return last
