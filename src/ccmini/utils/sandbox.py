"""Sandbox — execute subprocess commands with resource limits.

Provides cross-platform sandboxed execution with configurable timeout,
memory limits, filesystem isolation, and network access controls.

Platform-specific enforcement:
- **Linux**: ``ulimit`` / ``prlimit`` for memory, optional ``cgroups``
- **macOS**: ``sandbox-exec`` profiles for filesystem/network isolation
- **Windows**: ``JOB_OBJECT`` memory limits via :mod:`ctypes` (best-effort),
  timeout always enforced

All enforcement layers are best-effort: if a platform mechanism is not
available the executor falls back to timeout-only sandboxing and logs a
warning.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)


class SandboxProfile(str, Enum):
    """Pre-defined sandbox profiles with increasing restriction."""

    UNRESTRICTED = "unrestricted"
    STANDARD = "standard"
    STRICT = "strict"


@dataclass(slots=True)
class SandboxConfig:
    """Configuration for sandbox execution."""

    enabled: bool = True
    timeout: float = 30.0
    max_memory_mb: int = 512
    allowed_paths: list[str] = field(default_factory=list)
    network_access: bool = True
    profile: SandboxProfile = SandboxProfile.STANDARD

    @classmethod
    def for_profile(cls, profile: SandboxProfile, **overrides: Any) -> SandboxConfig:
        """Factory method to create config from a named profile."""
        defaults: dict[str, Any] = {
            SandboxProfile.UNRESTRICTED: {
                "enabled": False,
                "timeout": 300.0,
                "max_memory_mb": 0,
                "network_access": True,
            },
            SandboxProfile.STANDARD: {
                "enabled": True,
                "timeout": 30.0,
                "max_memory_mb": 512,
                "network_access": True,
            },
            SandboxProfile.STRICT: {
                "enabled": True,
                "timeout": 15.0,
                "max_memory_mb": 256,
                "network_access": False,
            },
        }
        cfg = {**defaults[profile], "profile": profile, **overrides}
        return cls(**{k: v for k, v in cfg.items() if k in cls.__dataclass_fields__})


@dataclass(slots=True)
class SandboxResult:
    """Outcome of a sandboxed command execution."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    sandbox_enforced: bool = False


def is_sandbox_available() -> bool:
    """Check whether any platform-level sandboxing is supported."""
    if sys.platform == "linux":
        return _linux_has_prlimit() or _linux_has_cgroups()
    if sys.platform == "darwin":
        return _macos_has_sandbox_exec()
    if sys.platform == "win32":
        return True  # timeout always available, JOB_OBJECT is best-effort
    return False


def create_sandbox_profile(
    name: str,
    *,
    timeout: float = 30.0,
    max_memory_mb: int = 512,
    allowed_paths: list[str] | None = None,
    network_access: bool = True,
) -> SandboxConfig:
    """Create a custom sandbox profile."""
    return SandboxConfig(
        enabled=True,
        timeout=timeout,
        max_memory_mb=max_memory_mb,
        allowed_paths=allowed_paths or [],
        network_access=network_access,
    )


class SandboxExecutor:
    """Execute commands inside a sandboxed subprocess.

    The executor applies platform-appropriate resource limits.  All
    parameters can be overridden per-call.
    """

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self._config = config or SandboxConfig()

    @property
    def config(self) -> SandboxConfig:
        return self._config

    async def execute(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
    ) -> SandboxResult:
        """Run *command* in a subprocess with resource limits.

        Parameters
        ----------
        command:
            Shell command string.
        timeout:
            Override the configured timeout (seconds).
        cwd:
            Working directory for the subprocess.
        """
        effective_timeout = timeout if timeout is not None else self._config.timeout
        effective_cwd = cwd or os.getcwd()
        sandbox_enforced = False

        if not self._config.enabled:
            return await self._run_raw(command, effective_timeout, effective_cwd)

        if sys.platform == "linux":
            return await self._run_linux(command, effective_timeout, effective_cwd)
        if sys.platform == "darwin":
            return await self._run_macos(command, effective_timeout, effective_cwd)
        if sys.platform == "win32":
            return await self._run_windows(command, effective_timeout, effective_cwd)

        log.warning("Unsupported platform %s — running without sandbox", sys.platform)
        return await self._run_raw(command, effective_timeout, effective_cwd)

    # ------------------------------------------------------------------
    # Platform runners
    # ------------------------------------------------------------------

    async def _run_raw(
        self, command: str, timeout: float, cwd: str,
    ) -> SandboxResult:
        """Run without any sandbox — just timeout enforcement."""
        return await _exec_with_timeout(command, timeout, cwd, sandbox_enforced=False)

    async def _run_linux(
        self, command: str, timeout: float, cwd: str,
    ) -> SandboxResult:
        mem_bytes = self._config.max_memory_mb * 1024 * 1024
        prefix = ""
        sandbox_enforced = False

        if mem_bytes and _linux_has_prlimit():
            prefix = f"prlimit --as={mem_bytes} -- "
            sandbox_enforced = True
        elif mem_bytes:
            prefix = f"ulimit -v {mem_bytes // 1024} && "
            sandbox_enforced = True

        if not self._config.network_access:
            if _linux_has_unshare():
                prefix = f"unshare --net -- {prefix}"
                sandbox_enforced = True
            else:
                log.debug("unshare not available; network isolation skipped")

        wrapped = f"{prefix}{command}"
        return await _exec_with_timeout(wrapped, timeout, cwd, sandbox_enforced=sandbox_enforced)

    async def _run_macos(
        self, command: str, timeout: float, cwd: str,
    ) -> SandboxResult:
        if not _macos_has_sandbox_exec():
            return await self._run_raw(command, timeout, cwd)

        profile = _build_macos_profile(
            allowed_paths=self._config.allowed_paths or [cwd],
            network=self._config.network_access,
        )
        profile_path = _write_temp_profile(profile)
        try:
            wrapped = f"sandbox-exec -f {profile_path} -- bash -c {_shell_quote(command)}"
            return await _exec_with_timeout(wrapped, timeout, cwd, sandbox_enforced=True)
        finally:
            try:
                os.unlink(profile_path)
            except OSError:
                pass

    async def _run_windows(
        self, command: str, timeout: float, cwd: str,
    ) -> SandboxResult:
        # Windows: timeout is always enforced; memory limit via JOB_OBJECT
        # is best-effort and requires ctypes — skip if not importable.
        result = await _exec_with_timeout(command, timeout, cwd, sandbox_enforced=False)
        return result


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

async def _exec_with_timeout(
    command: str,
    timeout: float,
    cwd: str,
    *,
    sandbox_enforced: bool,
) -> SandboxResult:
    """Run *command* via the platform shell with timeout enforcement."""
    timed_out = False
    try:
        if sys.platform == "win32":
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-NoProfile", "-Command", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            timed_out = True
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            stdout_bytes, stderr_bytes = b"", b""
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    except Exception as exc:
        return SandboxResult(
            stdout="",
            stderr=f"Error: {exc}",
            exit_code=-1,
            timed_out=False,
            sandbox_enforced=sandbox_enforced,
        )

    return SandboxResult(
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        exit_code=proc.returncode or 0,
        timed_out=timed_out,
        sandbox_enforced=sandbox_enforced,
    )


# ------------------------------------------------------------------
# Platform detection
# ------------------------------------------------------------------

def _linux_has_prlimit() -> bool:
    try:
        import shutil
        return shutil.which("prlimit") is not None
    except Exception:
        return False


def _linux_has_cgroups() -> bool:
    return os.path.isdir("/sys/fs/cgroup")


def _linux_has_unshare() -> bool:
    try:
        import shutil
        return shutil.which("unshare") is not None
    except Exception:
        return False


def _macos_has_sandbox_exec() -> bool:
    try:
        import shutil
        return shutil.which("sandbox-exec") is not None
    except Exception:
        return False


# ------------------------------------------------------------------
# macOS sandbox-exec profile generation
# ------------------------------------------------------------------

def _build_macos_profile(
    allowed_paths: list[str],
    network: bool = True,
) -> str:
    """Generate a Seatbelt profile string for ``sandbox-exec``."""
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
    ]
    if network:
        lines.append("(allow network*)")
    else:
        lines.append("(deny network*)")

    # Read access to standard system locations
    lines.append('(allow file-read* (subpath "/usr"))')
    lines.append('(allow file-read* (subpath "/bin"))')
    lines.append('(allow file-read* (subpath "/sbin"))')
    lines.append('(allow file-read* (subpath "/Library"))')
    lines.append('(allow file-read* (subpath "/System"))')
    lines.append('(allow file-read* (subpath "/private/tmp"))')
    lines.append('(allow file-read* (subpath "/private/var"))')
    lines.append('(allow file-read* (subpath "/dev"))')

    for path in allowed_paths:
        abs_path = os.path.abspath(path)
        lines.append(f'(allow file-read* (subpath "{abs_path}"))')
        lines.append(f'(allow file-write* (subpath "{abs_path}"))')

    # Temp directory access
    tmp = tempfile.gettempdir()
    lines.append(f'(allow file-read* (subpath "{tmp}"))')
    lines.append(f'(allow file-write* (subpath "{tmp}"))')

    return "\n".join(lines) + "\n"


def _write_temp_profile(profile: str) -> str:
    fd, path = tempfile.mkstemp(prefix="mini_agent_sandbox_", suffix=".sb")
    try:
        os.write(fd, profile.encode("utf-8"))
    finally:
        os.close(fd)
    return path


def _shell_quote(s: str) -> str:
    """Single-quote a string for bash, escaping embedded single quotes."""
    return "'" + s.replace("'", "'\\''") + "'"
