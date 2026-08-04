"""JSONL RPC client for `pi --mode rpc` subprocess."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shlex
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

EventListener = Callable[[dict[str, Any]], None]


@dataclass
class PiRpcClientOptions:
    """Launch options for the Pi RPC subprocess."""

    pi_bin: str = "pi"
    extension_path: str = ""
    cwd: str | Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    provider: str | None = None
    model: str | None = None
    extra_args: list[str] = field(default_factory=list)
    no_session: bool = True


def resolve_pi_executable(pi_bin: str | Path | None = None) -> str:
    """Resolve a Pi binary path that Windows CreateProcess can actually launch.

    npm installs ``pi`` as a Unix shell shim plus ``pi.cmd``. Bare ``pi`` works
    in bash/cmd shells via PATHEXT/shims, but ``asyncio.create_subprocess_exec``
    on Windows does not apply that lookup and raises ``FileNotFoundError``.
    Prefer an absolute path from ``shutil.which`` (usually ``...\\pi.CMD``).
    """
    raw = str(pi_bin or os.environ.get("REACHY_PI_BIN") or "pi").strip() or "pi"
    candidate = Path(raw).expanduser()
    if candidate.is_file():
        return str(candidate)

    # Absolute/relative path that doesn't exist yet — keep as-is for clear errors.
    if os.path.dirname(raw):
        return raw

    found = shutil.which(raw)
    if found:
        return found

    if sys.platform == "win32":
        for alt in (f"{raw}.cmd", f"{raw}.CMD", f"{raw}.exe", f"{raw}.bat"):
            found_alt = shutil.which(alt)
            if found_alt:
                return found_alt
        # Common global npm layout when PATH lookup fails in restricted envs.
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            npm_cmd = Path(appdata) / "npm" / f"{raw}.cmd"
            if npm_cmd.is_file():
                return str(npm_cmd)

    return raw


class PiRpcClient:
    """Spawn Pi in RPC mode and speak strict JSONL over stdin/stdout."""

    def __init__(self, options: PiRpcClientOptions | None = None) -> None:
        self.options = options or PiRpcClientOptions()
        self._process: asyncio.subprocess.Process | None = None
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._request_id = 0
        self._listeners: list[EventListener] = []
        self._stderr = ""
        self._exit_error: Exception | None = None
        self._stdout_buffer = ""

    @property
    def stderr(self) -> str:
        return self._stderr

    @property
    def is_running(self) -> bool:
        proc = self._process
        return proc is not None and proc.returncode is None

    def on_event(self, listener: EventListener) -> Callable[[], None]:
        """Subscribe to agent events (non-response JSONL lines)."""
        self._listeners.append(listener)

        def _unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _unsubscribe

    async def start(self) -> None:
        """Start the Pi RPC process."""
        if self._process is not None:
            raise RuntimeError("PiRpcClient already started")

        self._exit_error = None
        self._stderr = ""
        self._stdout_buffer = ""
        cmd = self._build_command()
        env = {**os.environ, **self.options.env}
        cwd = str(self.options.cwd) if self.options.cwd is not None else None

        LOGGER.info("starting pi rpc: %s", " ".join(shlex.quote(part) for part in cmd))
        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"Pi executable not found: {cmd[0]!r}. "
                "Install `@earendil-works/pi-coding-agent` (global `pi`) or set "
                "REACHY_PI_BIN to the full path of pi.cmd / cli.js launcher."
            ) from exc
        assert self._process.stdout is not None
        assert self._process.stderr is not None
        self._stdout_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

        # Brief settle so a fast crash is visible to the caller.
        await asyncio.sleep(0.15)
        if self._process.returncode is not None:
            error = self._exit_error or RuntimeError(
                f"pi rpc exited early (code={self._process.returncode}). "
                f"Stderr: {self._stderr}"
            )
            await self.stop()
            raise error

    async def stop(self) -> None:
        """Terminate the Pi RPC process."""
        proc = self._process
        self._process = None
        if proc is None:
            return

        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.5)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=1.0)

        for task in (self._stdout_task, self._stderr_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._stdout_task = None
        self._stderr_task = None

        error = self._exit_error or RuntimeError("Pi RPC client stopped")
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def prompt(self, message: str) -> dict[str, Any]:
        """Send a prompt command (events follow asynchronously)."""
        return await self.send({"type": "prompt", "message": message})

    async def abort(self) -> dict[str, Any]:
        """Abort the current agent operation."""
        return await self.send({"type": "abort"})

    async def new_session(self) -> dict[str, Any]:
        """Start a new Pi session (clears conversation)."""
        return await self.send({"type": "new_session"})

    async def get_state(self) -> dict[str, Any]:
        """Return current RPC session state."""
        response = await self.send({"type": "get_state"})
        if not response.get("success", True):
            raise RuntimeError(response.get("error") or "get_state failed")
        return dict(response.get("data") or {})

    async def send(self, command: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
        """Send one JSONL command and wait for its response line."""
        proc = self._process
        if proc is None or proc.stdin is None:
            raise RuntimeError("PiRpcClient not started")
        if self._exit_error is not None:
            raise self._exit_error
        if proc.returncode is not None:
            raise RuntimeError(
                f"pi rpc process exited (code={proc.returncode}). Stderr: {self._stderr}"
            )

        self._request_id += 1
        req_id = f"req_{self._request_id}"
        payload = {**command, "id": req_id}
        line = json.dumps(payload, ensure_ascii=False) + "\n"

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[req_id] = future
        try:
            proc.stdin.write(line.encode("utf-8"))
            await proc.stdin.drain()
            return await asyncio.wait_for(future, timeout=timeout)
        except Exception:
            self._pending.pop(req_id, None)
            raise

    async def wait_for_event(
        self,
        event_type: str,
        *,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Wait until an event of the given type is observed."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()

        def _listener(event: dict[str, Any]) -> None:
            if event.get("type") == event_type and not future.done():
                future.set_result(event)

        unsubscribe = self.on_event(_listener)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            unsubscribe()

    async def wait_for_settled(self, *, timeout: float = 60.0) -> dict[str, Any]:
        """Wait for agent_settled."""
        return await self.wait_for_event("agent_settled", timeout=timeout)

    def _build_command(self) -> list[str]:
        cmd = [resolve_pi_executable(self.options.pi_bin), "--mode", "rpc"]
        if self.options.no_session:
            cmd.append("--no-session")
        if self.options.provider:
            cmd.extend(["--provider", self.options.provider])
        if self.options.model:
            cmd.extend(["--model", self.options.model])
        if self.options.extension_path:
            cmd.extend(["-e", str(self.options.extension_path)])
        cmd.extend(self.options.extra_args)
        return cmd

    async def _read_stdout(self) -> None:
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                self._stdout_buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in self._stdout_buffer:
                    line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
                    if line.endswith("\r"):
                        line = line[:-1]
                    if line.strip():
                        self._handle_line(line)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover
            LOGGER.exception("pi rpc stdout reader failed")
            self._exit_error = exc
        finally:
            if proc.returncode is None:
                # EOF while process still listed running; record exit shortly.
                try:
                    await proc.wait()
                except Exception:
                    pass
            if self._exit_error is None and proc.returncode not in (None, 0):
                self._exit_error = RuntimeError(
                    f"pi rpc exited (code={proc.returncode}). Stderr: {self._stderr}"
                )
            error = self._exit_error or RuntimeError("pi rpc stdout closed")
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(error)
            self._pending.clear()

    async def _read_stderr(self) -> None:
        proc = self._process
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                self._stderr += text
                LOGGER.debug("pi rpc stderr: %s", text.rstrip())
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            LOGGER.exception("pi rpc stderr reader failed")

    def _handle_line(self, line: str) -> None:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            LOGGER.debug("pi rpc non-json line: %s", line[:200])
            return
        if not isinstance(data, dict):
            return

        if data.get("type") == "response" and data.get("id") in self._pending:
            future = self._pending.pop(str(data["id"]))
            if not future.done():
                future.set_result(data)
            return

        for listener in list(self._listeners):
            try:
                listener(data)
            except Exception:  # pragma: no cover
                LOGGER.exception("pi rpc event listener failed")


__all__ = ["PiRpcClient", "PiRpcClientOptions", "resolve_pi_executable"]
