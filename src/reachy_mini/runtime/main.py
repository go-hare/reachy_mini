"""CLI entry point for Reachy Mini app runtime tools."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from reachy_mini.pipeline.frames import (
    ActionResultFrame,
    PipelineErrorFrame,
    SDKMessageFrame,
)
from reachy_mini.pipeline.session import RuntimeSession
from reachy_mini.reachy_brain.pipecat_bridge import extract_text_blocks
from reachy_mini.runtime.project import (
    create_app_project,
    inspect_app_project,
    normalize_app_name,
)
from reachy_mini.runtime.web import build_web_host, resolve_web_binding, run_web_host

EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}


def _add_apps_root_argument(parser: argparse.ArgumentParser) -> None:
    """Add the app-project root argument with a hidden legacy alias."""
    parser.add_argument(
        "--apps-root",
        dest="apps_root",
        type=Path,
        default=Path("profiles"),
        help="Directory that stores app projects (defaults to profiles/).",
    )
    parser.add_argument(
        "--profiles-root",
        dest="apps_root",
        type=Path,
        help=argparse.SUPPRESS,
    )


def _get_apps_root(args: argparse.Namespace) -> Path:
    """Resolve the configured app-project root from parsed args."""
    return Path(
        getattr(
            args,
            "apps_root",
            getattr(args, "profiles_root", Path("profiles")),
        )
    )


def parse_args() -> argparse.Namespace:
    """Parse the ``reachy-mini-agent`` command line."""
    parser = argparse.ArgumentParser(
        description="Reachy Mini app runtime tools.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser(
        "create",
        help="Create an installable app project under profiles/.",
    )
    create_parser.add_argument("app_name", help="App name to create.")
    _add_apps_root_argument(create_parser)

    agent_parser = subparsers.add_parser(
        "agent",
        help="Run an app through the v4 RuntimeSession in text mode.",
    )
    agent_parser.add_argument(
        "app",
        help="App name or explicit app path.",
    )
    _add_apps_root_argument(agent_parser)
    agent_parser.add_argument(
        "--message",
        "-m",
        default="",
        help="Send one message and exit.",
    )
    agent_parser.add_argument(
        "--thread-id",
        default="",
        help="Override the generated SDK session/thread id for one-shot mode.",
    )
    agent_parser.add_argument(
        "--override",
        action="append",
        default=[],
        help="Override AgentConfig with key=value, e.g. model.temperature=0.0.",
    )
    agent_parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level for the v4 runner.",
    )

    web_parser = subparsers.add_parser(
        "web",
        help="Run an app's web UI and resident v4 runtime without connecting hardware.",
    )
    web_parser.add_argument(
        "app",
        help="App name or explicit app path.",
    )
    _add_apps_root_argument(web_parser)
    web_parser.add_argument(
        "--host",
        default=None,
        help="Override the bind host from the generated app's custom_app_url.",
    )
    web_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override the bind port from the generated app's custom_app_url.",
    )
    web_parser.add_argument(
        "--startup-timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for the resident runtime before failing.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the ``reachy-mini-agent`` CLI."""
    args = parse_args()
    if args.command == "create":
        handle_create(args)
        return
    if args.command == "agent":
        asyncio.run(handle_agent(args))
        return
    if args.command == "web":
        handle_web(args)
        return


def handle_create(args: argparse.Namespace) -> None:
    """Create an app project."""
    raw_app_name = str(args.app_name or "").strip()
    try:
        app_name = normalize_app_name(raw_app_name)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    target = _get_apps_root(args).expanduser().resolve() / app_name
    created = create_app_project(target, app_name)
    print(f"Created app: {created}")


async def handle_agent(args: argparse.Namespace) -> None:
    """Run a v4 RuntimeSession turn (or a small REPL)."""
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO)
    )
    app_path = resolve_app_path(args.app, _get_apps_root(args))
    profile_path = _resolve_profile_path(app_path)
    overrides = _parse_overrides(list(args.override or []))
    one_shot = bool(str(args.message or "").strip())
    session = _build_agent_session(profile_path, overrides=overrides)
    await session.start()
    try:
        if one_shot:
            await _run_one_turn(
                session,
                user_text=args.message.strip(),
                turn_id=str(args.thread_id or "") or None,
            )
            return
        await _run_interactive(session)
    finally:
        await session.stop()


def _build_agent_session(
    profile_path: Path,
    *,
    overrides: dict[str, Any],
) -> RuntimeSession:
    """Build the CLI RuntimeSession with the real Claude Agent SDK default."""
    return RuntimeSession.from_profile(profile_path, overrides=overrides)


def handle_web(args: argparse.Namespace) -> None:
    """Run the host-only web UI for one app project."""
    app_path = resolve_app_path(args.app, _get_apps_root(args))
    app_project = inspect_app_project(app_path)
    binding = resolve_web_binding(
        app_project,
        host=args.host,
        port=args.port,
    )
    app = build_web_host(app_project, bind_url=binding.bind_url)
    print(
        f"Serving {app_project.name} at {binding.browser_url} "
        f"(bind {binding.host}:{binding.port})"
    )
    run_web_host(
        app,
        host=binding.host,
        port=binding.port,
        startup_timeout=args.startup_timeout,
    )


def resolve_app_path(app: str, apps_root: Path) -> Path:
    """Resolve an app name or explicit path."""
    explicit = Path(app).expanduser()
    if explicit.exists():
        return explicit.resolve()
    return (apps_root.expanduser().resolve() / app).resolve()


def _resolve_profile_path(app_path: Path) -> Path:
    """Find the v4 profile directory under an app project."""
    candidate = app_path / "profiles"
    if candidate.is_dir():
        return candidate
    return app_path


def _parse_overrides(values: list[str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for value in values:
        key, sep, raw = value.partition("=")
        if not sep:
            raise SystemExit(f"Invalid override, expected key=value: {value}")
        overrides[key] = _coerce(raw)
    return overrides


def _coerce(raw: str) -> Any:
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


async def _run_one_turn(
    session: RuntimeSession,
    *,
    user_text: str,
    turn_id: str | None,
) -> None:
    """Submit one message and print SDK assistant text + action results."""
    sub = session.subscribe(
        filter=lambda frame: isinstance(
            frame, (SDKMessageFrame, ActionResultFrame, PipelineErrorFrame)
        )
    )
    try:
        actual_turn = await session.submit_text(user_text, turn_id=turn_id)
        await session.wait_for_turn_idle(actual_turn, timeout=30.0)
        # Drain whatever the bus collected for that turn.
        while not sub.queue.empty():
            frame = await sub.queue.get()
            if isinstance(frame, SDKMessageFrame):
                if frame.turn_id and frame.turn_id != actual_turn:
                    continue
                for text in extract_text_blocks(frame.message):
                    print(text)
            elif isinstance(frame, ActionResultFrame):
                print(
                    f"action_result: {frame.name} "
                    f"{frame.status} ({frame.duration_ms}ms)"
                )
            elif isinstance(frame, PipelineErrorFrame):
                print(f"pipeline_error: {frame.component}: {frame.reason}")
    finally:
        session.unsubscribe(sub)


async def _run_interactive(session: RuntimeSession) -> None:
    """Run a small text REPL backed by the v4 RuntimeSession."""
    print("Reachy Mini v4 interactive text mode (type exit or Ctrl+C to quit)")
    while True:
        try:
            raw = await asyncio.to_thread(input, "You: ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        user_text = str(raw or "").strip()
        if not user_text:
            continue
        if user_text.lower() in EXIT_COMMANDS:
            return
        await _run_one_turn(session, user_text=user_text, turn_id=None)


if __name__ == "__main__":
    main()
