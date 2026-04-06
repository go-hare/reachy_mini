"""Standalone launcher for the local ccmini frontend."""

from __future__ import annotations

import argparse
import contextlib
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _package_root() -> Path:
    return Path(__file__).resolve().parent


def _frontend_dir() -> Path:
    return _package_root() / "frontend"


def _resolve_bun() -> str:
    bun = shutil.which("bun")
    if bun:
        return bun
    raise SystemExit(
        "Unable to find `bun` in PATH. Install Bun first so ccmini can launch the frontend.",
    )


def _frontend_command(extra_args: list[str]) -> list[str]:
    return [
        _resolve_bun(),
        "run",
        "start",
        "--",
        "--local-backend",
        *extra_args,
    ]


def _self_command(extra_args: list[str]) -> list[str]:
    return [
        sys.executable,
        "-m",
        "ccmini",
        "--current-terminal",
        "--",
        *extra_args,
    ]


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _write_launcher_script(command: list[str], cwd: Path) -> Path:
    fd, path_str = tempfile.mkstemp(prefix="ccmini-launch-", suffix=".sh")
    path = Path(path_str)
    env_lines = [
        f"export PYTHON={shlex.quote(sys.executable)}",
        f"export CCMINI_PACKAGE_ROOT={shlex.quote(str(_package_root()))}",
    ]
    shell_command = " ".join(shlex.quote(part) for part in command)
    script = "\n".join(
        [
            "#!/bin/sh",
            "set -e",
            "trap 'rm -f \"$0\"' EXIT",
            *env_lines,
            f"cd {shlex.quote(str(cwd))}",
            f"exec {shell_command}",
            "",
        ]
    )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(script)
    path.chmod(0o700)
    return path


def _launch_in_new_terminal(command: list[str], cwd: Path) -> int:
    script_path = _write_launcher_script(command, cwd)
    launch_command = f"/bin/sh {shlex.quote(str(script_path))}"
    applescript = (
        'tell application "Terminal"\n'
        "activate\n"
        f'do script "{_escape_applescript(launch_command)}"\n'
        "end tell"
    )
    subprocess.run(["osascript", "-e", applescript], check=True)
    return 0


def _list_child_pids(pid: int) -> list[int]:
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(pid)],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []
    if result.returncode not in (0, 1):
        return []
    children: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            children.append(int(line))
        except ValueError:
            continue
    return children


def _terminate_process_tree(
    pid: int,
    *,
    sig: int = signal.SIGTERM,
    include_root: bool = True,
) -> None:
    for child_pid in _list_child_pids(pid):
        _terminate_process_tree(child_pid, sig=sig, include_root=True)
    if include_root:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, sig)


def _run_in_current_terminal(command: list[str], cwd: Path) -> int:
    env = dict(os.environ)
    env["PYTHON"] = sys.executable
    env["CCMINI_PACKAGE_ROOT"] = str(_package_root())

    child = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=None,
        stdout=None,
        stderr=None,
    )

    def _forward(sig_num: int, _frame: object | None) -> None:
        if child.poll() is not None:
            return
        _terminate_process_tree(child.pid, sig=sig_num, include_root=True)

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _forward)
    signal.signal(signal.SIGTERM, _forward)

    try:
        return child.wait()
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
        if child.poll() is None:
            _terminate_process_tree(child.pid, sig=signal.SIGTERM, include_root=True)
            deadline = time.time() + 1.0
            while child.poll() is None and time.time() < deadline:
                time.sleep(0.05)
        if child.poll() is None:
            _terminate_process_tree(child.pid, sig=signal.SIGKILL, include_root=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ccmini",
        description=(
            "Launch the local ccmini frontend in the current terminal and use "
            "the embedded backend by default."
        ),
    )
    parser.add_argument(
        "--current-terminal",
        action="store_true",
        help="Run in the current terminal. This is already the default.",
    )
    parser.add_argument(
        "--new-terminal",
        action="store_true",
        help="On macOS, open the frontend in a new Terminal window instead.",
    )
    parser.add_argument(
        "frontend_args",
        nargs=argparse.REMAINDER,
        help="Additional arguments forwarded to `bun run start -- --local-backend`.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    frontend_dir = _frontend_dir()
    if not frontend_dir.exists():
        raise SystemExit(f"Frontend directory not found: {frontend_dir}")

    forwarded = list(args.frontend_args)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]

    command = _frontend_command(forwarded)

    if sys.platform == "darwin" and args.new_terminal:
        try:
            return _launch_in_new_terminal(_self_command(forwarded), frontend_dir)
        except Exception as exc:
            print(
                f"Falling back to the current terminal because Terminal launch failed: {exc}",
                file=sys.stderr,
            )

    return _run_in_current_terminal(command, frontend_dir)


if __name__ == "__main__":
    raise SystemExit(main())
