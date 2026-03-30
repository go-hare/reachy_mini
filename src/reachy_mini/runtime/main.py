"""CLI entry point for Reachy Mini app runtime tools."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from reachy_mini.runtime.config import (
    apply_runtime_overrides,
    load_profile_runtime_config,
)
from reachy_mini.runtime.profile_loader import load_profile_bundle
from reachy_mini.runtime.scheduler import FrontOutputPacket, RuntimeScheduler
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
        help="Run an app through the front -> kernel -> text pipeline.",
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
        default="cli:main",
        help="Thread id used for session memory.",
    )
    agent_parser.add_argument(
        "--provider",
        choices=["mock", "openai", "ollama"],
        default=None,
        help="Override the front model provider from config.jsonl.",
    )
    agent_parser.add_argument(
        "--model",
        default=None,
        help="Override the front model name from config.jsonl.",
    )
    agent_parser.add_argument(
        "--base-url",
        default=None,
        help="Override the provider base URL.",
    )
    agent_parser.add_argument(
        "--api-key",
        default=None,
        help="Override the front model API key from config.jsonl.",
    )
    agent_parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Override the front model temperature.",
    )
    agent_parser.add_argument(
        "--kernel-provider",
        choices=["mock", "openai", "ollama"],
        default=None,
        help="Override the kernel model provider from config.jsonl.",
    )
    agent_parser.add_argument(
        "--kernel-model",
        default=None,
        help="Override the kernel model name from config.jsonl.",
    )
    agent_parser.add_argument(
        "--kernel-base-url",
        default=None,
        help="Override the kernel provider base URL.",
    )
    agent_parser.add_argument(
        "--kernel-api-key",
        default=None,
        help="Override the kernel model API key from config.jsonl.",
    )
    agent_parser.add_argument(
        "--kernel-temperature",
        type=float,
        default=None,
        help="Override the kernel model temperature.",
    )
    agent_parser.add_argument(
        "--history-limit",
        type=int,
        default=None,
        help="Override how many recent turns the front sees.",
    )

    web_parser = subparsers.add_parser(
        "web",
        help="Run an app's web UI and resident runtime without connecting hardware.",
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
    """Run the text app command."""
    app_path = resolve_app_path(args.app, _get_apps_root(args))
    profile_bundle = load_profile_bundle(app_path)
    config = apply_runtime_overrides(
        load_profile_runtime_config(profile_bundle),
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        temperature=args.temperature,
        kernel_provider=args.kernel_provider,
        kernel_model=args.kernel_model,
        kernel_base_url=args.kernel_base_url,
        kernel_api_key=args.kernel_api_key,
        kernel_temperature=args.kernel_temperature,
        history_limit=args.history_limit,
    )
    runtime = RuntimeScheduler.from_profile(
        profile=profile_bundle,
        config=config,
    )
    await runtime.start()
    try:
        if str(args.message or "").strip():
            await run_one_turn(
                runtime,
                thread_id=args.thread_id,
                user_text=args.message.strip(),
            )
            return

        await run_interactive(runtime, thread_id=args.thread_id)
    finally:
        await runtime.stop()


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


async def run_one_turn(
    runtime: RuntimeScheduler,
    *,
    thread_id: str,
    user_text: str,
) -> None:
    """Send one user message and print the reply."""
    printer = CliPrinter()
    queue = runtime.subscribe_front_outputs()
    pump_task = asyncio.create_task(
        pump_cli_front_outputs(queue=queue, printer=printer, thread_id=thread_id)
    )
    try:
        await runtime.handle_user_turn(
            thread_id=thread_id,
            session_id=thread_id,
            user_id="user",
            user_text=user_text,
        )
        await runtime.wait_for_thread_idle(thread_id)
        await asyncio.wait_for(queue.join(), timeout=1.0)
    finally:
        runtime.unsubscribe_front_outputs(queue)
        pump_task.cancel()
        try:
            await pump_task
        except asyncio.CancelledError:
            pass


async def run_interactive(runtime: RuntimeScheduler, *, thread_id: str) -> None:
    """Run a small interactive REPL."""
    print("Reachy Mini interactive text mode (type exit or Ctrl+C to quit)")
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
        await run_one_turn(runtime, thread_id=thread_id, user_text=user_text)


async def pump_cli_front_outputs(
    *,
    queue: asyncio.Queue[FrontOutputPacket],
    printer: "CliPrinter",
    thread_id: str,
) -> None:
    """Render runtime output packets to the CLI."""
    try:
        while True:
            packet = await queue.get()
            try:
                if packet.thread_id != thread_id:
                    continue
                if packet.type in {"front_hint_chunk", "front_final_chunk"}:
                    await printer.write_chunk(packet.text)
                    continue
                if packet.type in {"front_hint_done", "front_final_done"}:
                    if printer.stream_started:
                        printer.finish_stream()
                    elif packet.text:
                        printer.print_reply(packet.text)
                    continue
                if packet.type == "turn_error":
                    printer.print_error(packet.error)
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        raise


class CliPrinter:
    """Print streaming and final replies safely."""

    def __init__(self) -> None:
        """Track whether chunked output has started."""
        self.stream_started = False

    async def write_chunk(self, chunk: str) -> None:
        """Write one streamed chunk to stdout."""
        if not chunk:
            return
        self.stream_started = True
        print(chunk, end="", flush=True)

    def finish_stream(self) -> None:
        """Finish the streamed output block cleanly."""
        if not self.stream_started:
            return
        print()
        print()
        self.stream_started = False

    def print_reply(self, text: str) -> None:
        """Print one non-streamed reply."""
        print(text or "")
        print()

    def print_error(self, text: str) -> None:
        """Print one error block."""
        if self.stream_started:
            self.finish_stream()
        print(text or "")
        print()


if __name__ == "__main__":
    main()
