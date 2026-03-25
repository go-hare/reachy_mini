"""CLI entry point for the stage-2 front-only agent runtime."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from reachy_mini.agent_runtime.config import (
    apply_runtime_overrides,
    load_agent_profile_config,
)
from reachy_mini.agent_runtime.profile_loader import load_profile_workspace
from reachy_mini.agent_runtime.runner import FrontAgentRunner
from reachy_mini.agent_runtime.workspace import create_profile_workspace

EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}


def parse_args() -> argparse.Namespace:
    """Parse the ``reachy-mini-agent`` command line."""
    parser = argparse.ArgumentParser(
        description="Front-only Reachy Mini agent runtime.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser(
        "create",
        help="Create a standalone profile workspace under profiles/.",
    )
    create_parser.add_argument("profile_name", help="Profile name to create.")
    create_parser.add_argument(
        "--profiles-root",
        type=Path,
        default=Path("profiles"),
        help="Directory that stores standalone profile workspaces.",
    )

    agent_parser = subparsers.add_parser(
        "agent",
        help="Run the front-only profile -> front -> text runtime.",
    )
    agent_parser.add_argument(
        "profile",
        help="Profile name or explicit profile workspace path.",
    )
    agent_parser.add_argument(
        "--profiles-root",
        type=Path,
        default=Path("profiles"),
        help="Directory that stores standalone profile workspaces.",
    )
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
        "--api-key-env",
        default=None,
        help="Override the env var used for the OpenAI-compatible API key.",
    )
    agent_parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Override the front model temperature.",
    )
    agent_parser.add_argument(
        "--history-limit",
        type=int,
        default=None,
        help="Override how many recent turns the front sees.",
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


def handle_create(args: argparse.Namespace) -> None:
    """Create a standalone profile workspace."""
    profile_name = str(args.profile_name or "").strip()
    if not profile_name:
        raise SystemExit("profile_name cannot be empty")
    target = args.profiles_root.expanduser().resolve() / profile_name
    created = create_profile_workspace(target, profile_name)
    print(f"Created profile workspace: {created}")


async def handle_agent(args: argparse.Namespace) -> None:
    """Run the front-only agent command."""
    profile_path = resolve_profile_path(args.profile, args.profiles_root)
    profile = load_profile_workspace(profile_path)
    config = apply_runtime_overrides(
        load_agent_profile_config(profile),
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        temperature=args.temperature,
        history_limit=args.history_limit,
    )
    runner = FrontAgentRunner(profile=profile, config=config)

    if str(args.message or "").strip():
        await run_one_turn(
            runner,
            thread_id=args.thread_id,
            user_text=args.message.strip(),
        )
        return

    await run_interactive(runner, thread_id=args.thread_id)


def resolve_profile_path(profile: str, profiles_root: Path) -> Path:
    """Resolve a profile name or explicit path."""
    explicit = Path(profile).expanduser()
    if explicit.exists():
        return explicit.resolve()
    return (profiles_root.expanduser().resolve() / profile).resolve()


async def run_one_turn(
    runner: FrontAgentRunner,
    *,
    thread_id: str,
    user_text: str,
) -> None:
    """Send one user message and print the reply."""
    printer = CliPrinter()
    reply = await runner.reply(
        thread_id=thread_id,
        user_text=user_text,
        stream_handler=printer.write_chunk,
    )
    if printer.stream_started:
        printer.finish_stream()
        return
    printer.print_reply(reply)


async def run_interactive(runner: FrontAgentRunner, *, thread_id: str) -> None:
    """Run a small interactive REPL."""
    print("Reachy Mini agent interactive mode (type exit or Ctrl+C to quit)")
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
        await run_one_turn(runner, thread_id=thread_id, user_text=user_text)


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


if __name__ == "__main__":
    main()
