"""Computer Use — cross-platform desktop control abstraction.

Ported from Claude Code's ``utils/computerUse/`` subsystem.

Provides a unified API for screenshot, mouse, keyboard, and clipboard
operations across macOS, Linux, and Windows. Each platform has a
specialized executor; a no-op stub is used when the platform is
unsupported or dependencies are missing.

Session locking prevents multiple agents from controlling the desktop
simultaneously.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import platform
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..paths import mini_agent_path

logger = logging.getLogger(__name__)


# ── Types ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScreenshotResult:
    """Result of a screenshot operation."""
    image_base64: str
    width: int = 0
    height: int = 0
    format: str = "png"


@dataclass(frozen=True)
class ClickResult:
    success: bool
    message: str = ""


@dataclass(frozen=True)
class TypeResult:
    success: bool
    message: str = ""


# ── Executor protocol ────────────────────────────────────────────────

@runtime_checkable
class ComputerExecutor(Protocol):
    """Platform-specific desktop control implementation."""

    async def screenshot(self, *, region: tuple[int, int, int, int] | None = None) -> ScreenshotResult: ...
    async def mouse_move(self, x: int, y: int) -> None: ...
    async def mouse_click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> ClickResult: ...
    async def mouse_drag(self, x1: int, y1: int, x2: int, y2: int) -> None: ...
    async def type_text(self, text: str) -> TypeResult: ...
    async def key_press(self, *keys: str) -> TypeResult: ...
    async def get_clipboard(self) -> str: ...
    async def set_clipboard(self, text: str) -> None: ...
    async def get_screen_size(self) -> tuple[int, int]: ...


# ── Platform executors ───────────────────────────────────────────────


class _SubprocessExecutor:
    """Base for subprocess-backed executors."""

    async def _run(self, *args: str, input_data: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                args,
                capture_output=True,
                input=input_data,
                timeout=10,
            ),
        )


class MacOSExecutor(_SubprocessExecutor):
    """macOS executor using screencapture + cliclick/AppleScript."""

    async def screenshot(self, *, region: tuple[int, int, int, int] | None = None) -> ScreenshotResult:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        try:
            cmd = ["screencapture", "-x"]
            if region:
                x, y, w, h = region
                cmd.extend(["-R", f"{x},{y},{w},{h}"])
            cmd.append(tmp.name)
            await self._run(*cmd)
            data = Path(tmp.name).read_bytes()
            return ScreenshotResult(image_base64=base64.b64encode(data).decode())
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    async def mouse_move(self, x: int, y: int) -> None:
        await self._run_applescript(f'do shell script "cliclick m:{x},{y}" ')

    async def mouse_click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> ClickResult:
        action = "dc" if clicks == 2 else "c"
        if button == "right":
            action = "rc"
        if shutil.which("cliclick"):
            await self._run("cliclick", f"{action}:{x},{y}")
        else:
            script = f'tell application "System Events" to click at {{{x}, {y}}}'
            await self._run_applescript(script)
        return ClickResult(success=True)

    async def mouse_drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        if shutil.which("cliclick"):
            await self._run("cliclick", f"dd:{x1},{y1}", f"du:{x2},{y2}")

    async def type_text(self, text: str) -> TypeResult:
        if shutil.which("cliclick"):
            await self._run("cliclick", f"t:{text}")
        else:
            escaped = text.replace('"', '\\"')
            await self._run_applescript(
                f'tell application "System Events" to keystroke "{escaped}"'
            )
        return TypeResult(success=True)

    async def key_press(self, *keys: str) -> TypeResult:
        if shutil.which("cliclick"):
            combo = "+".join(keys)
            await self._run("cliclick", f"kp:{combo}")
        return TypeResult(success=True)

    async def get_clipboard(self) -> str:
        result = await self._run("pbpaste")
        return result.stdout.decode("utf-8", errors="replace")

    async def set_clipboard(self, text: str) -> None:
        await self._run("pbcopy", input_data=text.encode("utf-8"))

    async def get_screen_size(self) -> tuple[int, int]:
        result = await self._run(
            "python3", "-c",
            "from AppKit import NSScreen; s=NSScreen.mainScreen().frame(); print(int(s.size.width), int(s.size.height))"
        )
        parts = result.stdout.decode().strip().split()
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
        return 1920, 1080

    async def _run_applescript(self, script: str) -> subprocess.CompletedProcess[bytes]:
        return await self._run("osascript", "-e", script)


class LinuxExecutor(_SubprocessExecutor):
    """Linux executor using xdotool + scrot/gnome-screenshot."""

    async def screenshot(self, *, region: tuple[int, int, int, int] | None = None) -> ScreenshotResult:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        try:
            if shutil.which("scrot"):
                cmd = ["scrot", tmp.name]
                if region:
                    x, y, w, h = region
                    cmd = ["scrot", "-a", f"{x},{y},{w},{h}", tmp.name]
                await self._run(*cmd)
            elif shutil.which("gnome-screenshot"):
                await self._run("gnome-screenshot", "-f", tmp.name)
            elif shutil.which("import"):
                await self._run("import", "-window", "root", tmp.name)
            else:
                return ScreenshotResult(image_base64="", format="none")
            data = Path(tmp.name).read_bytes()
            return ScreenshotResult(image_base64=base64.b64encode(data).decode())
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    async def mouse_move(self, x: int, y: int) -> None:
        if shutil.which("xdotool"):
            await self._run("xdotool", "mousemove", str(x), str(y))

    async def mouse_click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> ClickResult:
        if not shutil.which("xdotool"):
            return ClickResult(success=False, message="xdotool not found")
        btn = {"left": "1", "middle": "2", "right": "3"}.get(button, "1")
        await self._run("xdotool", "mousemove", str(x), str(y))
        for _ in range(clicks):
            await self._run("xdotool", "click", btn)
        return ClickResult(success=True)

    async def mouse_drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        if shutil.which("xdotool"):
            await self._run("xdotool", "mousemove", str(x1), str(y1),
                          "mousedown", "1", "mousemove", str(x2), str(y2), "mouseup", "1")

    async def type_text(self, text: str) -> TypeResult:
        if shutil.which("xdotool"):
            await self._run("xdotool", "type", "--", text)
            return TypeResult(success=True)
        return TypeResult(success=False, message="xdotool not found")

    async def key_press(self, *keys: str) -> TypeResult:
        if shutil.which("xdotool"):
            combo = "+".join(keys)
            await self._run("xdotool", "key", combo)
            return TypeResult(success=True)
        return TypeResult(success=False, message="xdotool not found")

    async def get_clipboard(self) -> str:
        if shutil.which("xclip"):
            result = await self._run("xclip", "-selection", "clipboard", "-o")
            return result.stdout.decode("utf-8", errors="replace")
        if shutil.which("xsel"):
            result = await self._run("xsel", "--clipboard", "--output")
            return result.stdout.decode("utf-8", errors="replace")
        return ""

    async def set_clipboard(self, text: str) -> None:
        if shutil.which("xclip"):
            await self._run("xclip", "-selection", "clipboard", input_data=text.encode())
        elif shutil.which("xsel"):
            await self._run("xsel", "--clipboard", "--input", input_data=text.encode())

    async def get_screen_size(self) -> tuple[int, int]:
        if shutil.which("xdpyinfo"):
            result = await self._run("xdpyinfo")
            for line in result.stdout.decode().splitlines():
                if "dimensions:" in line:
                    parts = line.split()[1].split("x")
                    if len(parts) == 2:
                        return int(parts[0]), int(parts[1])
        return 1920, 1080


class WindowsExecutor(_SubprocessExecutor):
    """Windows executor using PowerShell + ctypes."""

    async def screenshot(self, *, region: tuple[int, int, int, int] | None = None) -> ScreenshotResult:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        try:
            ps_script = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
                "$bmp=New-Object Drawing.Bitmap($b.Width,$b.Height);"
                "$g=[Drawing.Graphics]::FromImage($bmp);"
                "$g.CopyFromScreen($b.Location,[Drawing.Point]::Empty,$b.Size);"
                f"$bmp.Save('{tmp.name}');"
                "$g.Dispose();$bmp.Dispose()"
            )
            await self._run("powershell", "-NoProfile", "-Command", ps_script)
            data = Path(tmp.name).read_bytes()
            return ScreenshotResult(image_base64=base64.b64encode(data).decode())
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    async def mouse_move(self, x: int, y: int) -> None:
        ps = f"Add-Type -AssemblyName System.Windows.Forms;[System.Windows.Forms.Cursor]::Position=New-Object Drawing.Point({x},{y})"
        await self._run("powershell", "-NoProfile", "-Command", ps)

    async def mouse_click(self, x: int, y: int, *, button: str = "left", clicks: int = 1) -> ClickResult:
        await self.mouse_move(x, y)
        ps = (
            "$sig='[DllImport(\"user32.dll\")] public static extern void mouse_event(int f,int dx,int dy,int d,int e);';"
            "$t=Add-Type -MemberDefinition $sig -Name U32 -Namespace W -PassThru;"
        )
        if button == "right":
            ps += "$t::mouse_event(0x08,0,0,0,0);$t::mouse_event(0x10,0,0,0,0)"
        else:
            for _ in range(clicks):
                ps += "$t::mouse_event(0x02,0,0,0,0);$t::mouse_event(0x04,0,0,0,0);"
        await self._run("powershell", "-NoProfile", "-Command", ps)
        return ClickResult(success=True)

    async def mouse_drag(self, x1: int, y1: int, x2: int, y2: int) -> None:
        await self.mouse_move(x1, y1)
        ps = (
            "$sig='[DllImport(\"user32.dll\")] public static extern void mouse_event(int f,int dx,int dy,int d,int e);';"
            "$t=Add-Type -MemberDefinition $sig -Name U32d -Namespace W -PassThru;"
            "$t::mouse_event(0x02,0,0,0,0);"
        )
        await self._run("powershell", "-NoProfile", "-Command", ps)
        await self.mouse_move(x2, y2)
        ps2 = (
            "$sig='[DllImport(\"user32.dll\")] public static extern void mouse_event(int f,int dx,int dy,int d,int e);';"
            "$t=Add-Type -MemberDefinition $sig -Name U32u -Namespace W -PassThru;"
            "$t::mouse_event(0x04,0,0,0,0);"
        )
        await self._run("powershell", "-NoProfile", "-Command", ps2)

    async def type_text(self, text: str) -> TypeResult:
        escaped = text.replace("'", "''")
        ps = f"Add-Type -AssemblyName System.Windows.Forms;[System.Windows.Forms.SendKeys]::SendWait('{escaped}')"
        await self._run("powershell", "-NoProfile", "-Command", ps)
        return TypeResult(success=True)

    async def key_press(self, *keys: str) -> TypeResult:
        combo = "".join(f"{{{k}}}" if len(k) > 1 else k for k in keys)
        ps = f"Add-Type -AssemblyName System.Windows.Forms;[System.Windows.Forms.SendKeys]::SendWait('{combo}')"
        await self._run("powershell", "-NoProfile", "-Command", ps)
        return TypeResult(success=True)

    async def get_clipboard(self) -> str:
        result = await self._run("powershell", "-NoProfile", "-Command", "Get-Clipboard")
        return result.stdout.decode("utf-8", errors="replace").strip()

    async def set_clipboard(self, text: str) -> None:
        escaped = text.replace("'", "''")
        await self._run("powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value '{escaped}'")

    async def get_screen_size(self) -> tuple[int, int]:
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$s=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
            "Write-Output \"$($s.Width) $($s.Height)\""
        )
        result = await self._run("powershell", "-NoProfile", "-Command", ps)
        parts = result.stdout.decode().strip().split()
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
        return 1920, 1080


# ── Session locking ──────────────────────────────────────────────────

_LOCK_DIR = mini_agent_path("computer_use")
_LOCK_FILE = _LOCK_DIR / "session.lock"


def acquire_session_lock(session_id: str) -> bool:
    """Acquire exclusive desktop control lock. Returns True on success."""
    _LOCK_DIR.mkdir(parents=True, exist_ok=True)
    if _LOCK_FILE.exists():
        try:
            content = _LOCK_FILE.read_text().strip()
            pid_str, _, _ = content.partition(":")
            pid = int(pid_str)
            if _pid_alive(pid):
                return False
        except (ValueError, OSError):
            pass
    try:
        _LOCK_FILE.write_text(f"{os.getpid()}:{session_id}")
        return True
    except OSError:
        return False


def release_session_lock() -> None:
    """Release the desktop control lock."""
    _LOCK_FILE.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    if platform.system() == "Windows":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


# ── Factory ──────────────────────────────────────────────────────────


def get_executor() -> ComputerExecutor:
    """Get the appropriate executor for the current platform."""
    info = check_computer_use_availability()
    system = info["platform"]
    if not info["available"]:
        raise RuntimeError(f"Computer Use is unavailable on platform: {system}")
    if system == "Darwin":
        return MacOSExecutor()  # type: ignore[return-value]
    if system == "Linux":
        return LinuxExecutor()  # type: ignore[return-value]
    if system == "Windows":
        return WindowsExecutor()  # type: ignore[return-value]
    raise RuntimeError(f"Computer Use executor not implemented for platform: {system}")


def check_computer_use_availability() -> dict[str, Any]:
    """Check platform support and available tools."""
    system = platform.system()
    tools: dict[str, bool] = {}
    available = False

    if system == "Darwin":
        tools["screencapture"] = shutil.which("screencapture") is not None
        tools["cliclick"] = shutil.which("cliclick") is not None
        tools["osascript"] = shutil.which("osascript") is not None
        available = tools["screencapture"] and (tools["cliclick"] or tools["osascript"])
    elif system == "Linux":
        tools["scrot"] = shutil.which("scrot") is not None
        tools["gnome-screenshot"] = shutil.which("gnome-screenshot") is not None
        tools["import"] = shutil.which("import") is not None
        tools["xdotool"] = shutil.which("xdotool") is not None
        tools["xclip"] = shutil.which("xclip") is not None
        available = (tools["scrot"] or tools["gnome-screenshot"] or tools["import"]) and tools["xdotool"]
    elif system == "Windows":
        tools["powershell"] = shutil.which("powershell") is not None
        available = tools["powershell"]

    return {
        "platform": system,
        "available": available,
        "tools": tools,
    }
