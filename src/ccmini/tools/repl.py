"""REPLTool — interactive REPL execution for Python, Node.js, etc.

Supports one-shot execution (run code and return result) and persistent
sessions (state preserved across calls).  Each session is a subprocess
with stdin/stdout piping.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..tool import Tool, ToolUseContext

_DEFAULT_TIMEOUT = 30
_MAX_OUTPUT_CHARS = 100_000

# ---------------------------------------------------------------------------
# Language configuration
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _LangConfig:
    name: str
    one_shot_cmd: list[str]  # [executable, flag] for one-shot: e.g. ["python", "-c"]
    interactive_cmd: list[str]  # Command to start an interactive session
    file_extensions: list[str]
    exit_command: str  # Command to send to cleanly exit the REPL
    prompt_marker: str  # Marker that indicates the REPL is ready for input


_LANGUAGES: dict[str, _LangConfig] = {
    "python": _LangConfig(
        name="python",
        one_shot_cmd=[sys.executable or "python", "-c"],
        interactive_cmd=[sys.executable or "python", "-i", "-q"],
        file_extensions=[".py"],
        exit_command="exit()\n",
        prompt_marker=">>> ",
    ),
    "node": _LangConfig(
        name="node",
        one_shot_cmd=["node", "-e"],
        interactive_cmd=["node", "-i"],
        file_extensions=[".js", ".mjs"],
        exit_command=".exit\n",
        prompt_marker="> ",
    ),
}

# Extension → language mapping for auto-detection
_EXT_TO_LANG: dict[str, str] = {}
for _lang, _cfg in _LANGUAGES.items():
    for _ext in _cfg.file_extensions:
        _EXT_TO_LANG[_ext] = _lang


def _detect_language(code: str) -> str:
    """Heuristic language detection from code content."""
    indicators_python = ["import ", "def ", "class ", "print(", "from ", "if __name__"]
    indicators_node = ["const ", "let ", "var ", "require(", "console.log", "function ", "=>"]

    py_score = sum(1 for ind in indicators_python if ind in code)
    js_score = sum(1 for ind in indicators_node if ind in code)

    if py_score > js_score:
        return "python"
    if js_score > py_score:
        return "node"
    return "python"


def _find_executable(lang: str) -> str | None:
    """Return the full path to the language's executable, or None."""
    cfg = _LANGUAGES.get(lang)
    if cfg is None:
        return None
    exe = cfg.one_shot_cmd[0]
    return shutil.which(exe)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@dataclass
class _REPLSession:
    """A persistent REPL subprocess."""
    session_id: str
    language: str
    process: asyncio.subprocess.Process
    created_at: float
    last_used: float
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def alive(self) -> bool:
        return self.process.returncode is None


# Global session registry
_sessions: dict[str, _REPLSession] = {}


async def start_session(language: str) -> tuple[str, str]:
    """Start a persistent REPL session.  Returns (session_id, message)."""
    cfg = _LANGUAGES.get(language)
    if cfg is None:
        return "", f"Error: Unsupported language '{language}'. Supported: {', '.join(_LANGUAGES)}"

    if _find_executable(language) is None:
        return "", f"Error: '{cfg.one_shot_cmd[0]}' not found on PATH."

    try:
        proc = await asyncio.create_subprocess_exec(
            *cfg.interactive_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except Exception as exc:
        return "", f"Error starting REPL: {exc}"

    session_id = uuid.uuid4().hex[:12]
    now = asyncio.get_event_loop().time()

    session = _REPLSession(
        session_id=session_id,
        language=language,
        process=proc,
        created_at=now,
        last_used=now,
    )
    _sessions[session_id] = session

    # Drain initial output (banner, prompt)
    await _drain_initial(session, timeout=5.0)

    return session_id, f"Started {language} REPL session: {session_id}"


async def _drain_initial(session: _REPLSession, timeout: float = 5.0) -> str:
    """Read and discard the initial REPL banner/prompt."""
    output: list[str] = []
    try:
        assert session.process.stdout is not None
        while True:
            chunk = await asyncio.wait_for(
                session.process.stdout.read(4096), timeout=timeout,
            )
            if not chunk:
                break
            output.append(chunk.decode("utf-8", errors="replace"))
            # If we got something and stdout seems quiet, break
            await asyncio.sleep(0.1)
            if session.process.stdout.at_eof():
                break
            # Try non-blocking read
            try:
                more = await asyncio.wait_for(session.process.stdout.read(4096), timeout=0.5)
                if more:
                    output.append(more.decode("utf-8", errors="replace"))
                else:
                    break
            except asyncio.TimeoutError:
                break
    except asyncio.TimeoutError:
        pass
    return "".join(output)


async def execute_in_session(
    session_id: str, code: str, timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Execute code in an existing session.  Returns the output."""
    session = _sessions.get(session_id)
    if session is None:
        return f"Error: Session '{session_id}' not found. Active sessions: {list(_sessions.keys())}"

    if not session.alive:
        del _sessions[session_id]
        return f"Error: Session '{session_id}' has ended (exit code {session.process.returncode})."

    async with session._lock:
        return await _send_code(session, code, timeout)


async def _send_code(
    session: _REPLSession, code: str, timeout: float,
) -> str:
    """Send code to the REPL and capture output until the next prompt."""
    assert session.process.stdin is not None
    assert session.process.stdout is not None

    # Use a sentinel to detect end of output
    sentinel = f"__REPL_DONE_{uuid.uuid4().hex[:8]}__"
    cfg = _LANGUAGES[session.language]

    if session.language == "python":
        # Send code followed by a print of the sentinel
        payload = code.rstrip("\n") + f"\nprint('{sentinel}')\n"
    elif session.language == "node":
        payload = code.rstrip("\n") + f"\nconsole.log('{sentinel}')\n"
    else:
        payload = code.rstrip("\n") + "\n"

    session.process.stdin.write(payload.encode("utf-8"))
    await session.process.stdin.drain()

    session.last_used = asyncio.get_event_loop().time()

    output_parts: list[str] = []
    try:
        while True:
            chunk = await asyncio.wait_for(
                session.process.stdout.read(4096), timeout=timeout,
            )
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            if sentinel in text:
                # Capture everything before the sentinel
                before = text.split(sentinel)[0]
                output_parts.append(before)
                break
            output_parts.append(text)
    except asyncio.TimeoutError:
        output_parts.append(f"\n(timed out after {timeout}s)")

    # Also capture any stderr
    stderr_text = ""
    if session.process.stderr:
        try:
            stderr_data = await asyncio.wait_for(
                session.process.stderr.read(4096), timeout=0.5,
            )
            if stderr_data:
                stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
        except asyncio.TimeoutError:
            pass

    result = "".join(output_parts).strip()

    # Clean up prompt markers from output
    for marker in (cfg.prompt_marker, "... "):
        result = result.replace(marker, "")
    result = result.strip()

    if stderr_text:
        result += f"\nSTDERR:\n{stderr_text}"

    return result if result else "(no output)"


async def end_session(session_id: str) -> str:
    """End a REPL session and clean up."""
    session = _sessions.pop(session_id, None)
    if session is None:
        return f"Error: Session '{session_id}' not found."

    if session.alive:
        cfg = _LANGUAGES.get(session.language)
        try:
            if cfg and session.process.stdin:
                session.process.stdin.write(cfg.exit_command.encode("utf-8"))
                await session.process.stdin.drain()
                await asyncio.wait_for(session.process.wait(), timeout=5.0)
        except Exception:
            pass

        if session.alive:
            try:
                session.process.kill()
            except ProcessLookupError:
                pass

    return f"Session '{session_id}' ended."


def list_sessions() -> list[dict[str, Any]]:
    """Return metadata for all active sessions."""
    result: list[dict[str, Any]] = []
    for sid, s in _sessions.items():
        result.append({
            "session_id": sid,
            "language": s.language,
            "alive": s.alive,
            "created_at": s.created_at,
            "last_used": s.last_used,
        })
    return result


# ---------------------------------------------------------------------------
# One-shot execution
# ---------------------------------------------------------------------------

async def _execute_oneshot(
    language: str, code: str, timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Run code in a fresh subprocess (no persistent state)."""
    cfg = _LANGUAGES.get(language)
    if cfg is None:
        return f"Error: Unsupported language '{language}'."

    if _find_executable(language) is None:
        return f"Error: '{cfg.one_shot_cmd[0]}' not found on PATH."

    try:
        proc = await asyncio.create_subprocess_exec(
            *cfg.one_shot_cmd, code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except (ProcessLookupError, UnboundLocalError):
            pass
        return f"Error: Execution timed out after {timeout}s"
    except Exception as exc:
        return f"Error: {exc}"

    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()

    parts: list[str] = []
    if stdout_text:
        parts.append(stdout_text)
    if stderr_text:
        parts.append(f"STDERR:\n{stderr_text}")
    if proc.returncode and proc.returncode != 0:
        parts.append(f"Exit code: {proc.returncode}")

    result = "\n".join(parts) if parts else "(no output)"
    return _truncate_output(result)


def _truncate_output(text: str, max_chars: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    keep = max_chars // 2
    removed = len(text) - max_chars
    return f"{text[:keep]}\n\n[...truncated {removed} chars...]\n\n{text[-keep:]}"


# ---------------------------------------------------------------------------
# REPLTool
# ---------------------------------------------------------------------------

class REPLTool(Tool):
    name = "REPL"
    description = (
        "Interactive REPL execution for Python, Node.js, etc. "
        "Supports one-shot execution and persistent sessions."
    )
    instructions = """\
Execute code in a REPL (Read-Eval-Print Loop).

## Modes

### One-shot (default)
Run code in a fresh subprocess. No state is preserved.
Set action="execute" with language and code.

### Session
Maintain state across multiple executions (variables, imports, etc.).
- action="start_session" — Start a new persistent REPL
- action="execute_in_session" — Run code in an existing session
- action="end_session" — Clean up a session
- action="list_sessions" — View active sessions

## Languages

- python: Uses the current Python interpreter
- node: Uses Node.js (must be installed)

Language is auto-detected from code content if not specified.

## Parameters

- action: "execute" (default), "start_session", "execute_in_session", \
"end_session", "list_sessions"
- language: "python" or "node" (auto-detected if omitted)
- code: The code to execute (for execute / execute_in_session)
- session_id: Session identifier (for execute_in_session / end_session)
- timeout: Execution timeout in seconds (default: 30)

## Notes

- One-shot mode is simpler and more reliable for independent snippets.
- Session mode is useful when you need to build up state incrementally \
(e.g. define a function, then call it).
- Sessions are cleaned up when ended or when the agent process exits.
- Output is captured from both stdout and stderr.\
"""
    is_read_only = False

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Operation to perform",
                    "enum": [
                        "execute",
                        "start_session",
                        "execute_in_session",
                        "end_session",
                        "list_sessions",
                    ],
                },
                "language": {
                    "type": "string",
                    "description": "Language: 'python' or 'node' (auto-detected if omitted)",
                    "enum": ["python", "node"],
                },
                "code": {
                    "type": "string",
                    "description": "Code to execute",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session ID (for session operations)",
                },
                "timeout": {
                    "type": "integer",
                    "description": f"Timeout in seconds (default: {_DEFAULT_TIMEOUT})",
                },
            },
            "required": ["action"],
        }

    async def execute(self, *, context: ToolUseContext, **kwargs: Any) -> str:
        action: str = kwargs.get("action", "execute")
        language: str | None = kwargs.get("language")
        code: str = kwargs.get("code", "")
        session_id: str = kwargs.get("session_id", "")
        timeout: int = kwargs.get("timeout", _DEFAULT_TIMEOUT)

        if action == "execute":
            return await self._oneshot(language, code, timeout)
        elif action == "start_session":
            return await self._start_session(language or "python")
        elif action == "execute_in_session":
            return await self._exec_in_session(session_id, code, timeout)
        elif action == "end_session":
            return await self._end_session(session_id)
        elif action == "list_sessions":
            return self._list_sessions()
        else:
            return f"Error: Unknown action '{action}'"

    async def _oneshot(self, language: str | None, code: str, timeout: int) -> str:
        if not code.strip():
            return "Error: 'code' is required for one-shot execution."
        lang = language or _detect_language(code)
        return await _execute_oneshot(lang, code, timeout=timeout)

    async def _start_session(self, language: str) -> str:
        session_id, message = await start_session(language)
        return message

    async def _exec_in_session(self, session_id: str, code: str, timeout: int) -> str:
        if not session_id:
            return "Error: 'session_id' is required for execute_in_session."
        if not code.strip():
            return "Error: 'code' is required."
        return await execute_in_session(session_id, code, timeout=timeout)

    async def _end_session(self, session_id: str) -> str:
        if not session_id:
            return "Error: 'session_id' is required for end_session."
        return await end_session(session_id)

    def _list_sessions(self) -> str:
        sessions = list_sessions()
        if not sessions:
            return "No active REPL sessions."
        lines = [f"Active sessions ({len(sessions)}):"]
        for s in sessions:
            status = "alive" if s["alive"] else "dead"
            lines.append(f"  {s['session_id']} ({s['language']}, {status})")
        return "\n".join(lines)
