"""Local ccmini frontend host runner.

Starts a local bridge-backed ccmini executor and prints one JSON line to stdout:
{"serverUrl": "...", "authToken": "..."}

The frontend can spawn this helper, read the ready payload, and then connect
through the existing /bridge/* flow without changing the UI protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import socket
import sys
from pathlib import Path


def _ensure_repo_src_on_path() -> None:
    current = Path(__file__).resolve()
    src_root = current.parent.parent
    src_root_str = str(src_root)
    if src_root_str not in sys.path:
        sys.path.insert(0, src_root_str)


_ensure_repo_src_on_path()

from ccmini.bridge import BridgeConfig, create_remote_executor_host
from ccmini.config import load_config
from ccmini.profiles import RuntimeProfile
from ccmini.prompt_defaults import build_default_prompt
from ccmini.providers import ProviderConfig

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start a local ccmini bridge host for the terminal frontend.",
    )
    parser.add_argument("--host", default=None, help="Override bridge bind host.")
    parser.add_argument("--port", type=int, default=None, help="Override bridge bind port.")
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Override bridge bearer token. Defaults to ccmini config or an ephemeral token.",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Override provider type (mock/openai/anthropic/compatible/ollama/vllm/deepseek).",
    )
    parser.add_argument("--model", default=None, help="Override model name.")
    parser.add_argument("--api-key", default=None, help="Override API key.")
    parser.add_argument("--base-url", default=None, help="Override provider base URL.")
    parser.add_argument(
        "--system-prompt",
        default=None,
        help="Override system prompt text. Defaults to ccmini config or build_default_prompt().",
    )
    parser.add_argument(
        "--profile",
        choices=[p.value for p in RuntimeProfile],
        default=RuntimeProfile.CODING_ASSISTANT.value,
        help="Agent runtime profile for the local host.",
    )
    return parser.parse_args()


def _build_ready_payload(host: BridgeConfig) -> dict[str, str]:
    scheme = "https" if host.ssl else "http"
    return {
        "serverUrl": f"{scheme}://{host.host}:{host.port}",
        "authToken": host.auth_token,
    }


def _port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _find_open_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


async def _run() -> int:
    args = _parse_args()
    cfg = load_config(
        cli_overrides={
            "ccmini_host": args.host,
            "ccmini_port": args.port,
            "ccmini_auth_token": args.auth_token,
            "provider": args.provider,
            "model": args.model,
            "api_key": args.api_key,
            "base_url": args.base_url,
            "system_prompt": args.system_prompt,
        }
    )

    provider = ProviderConfig(
        type=cfg.provider,
        model=cfg.model,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        max_tokens=cfg.max_tokens,
    )
    system_prompt = cfg.system_prompt or build_default_prompt()
    bridge_config = BridgeConfig(
        enabled=True,
        host=cfg.ccmini_host or "127.0.0.1",
        port=cfg.ccmini_port or 7779,
        auth_token=cfg.ccmini_auth_token,
    )
    if args.port is None and not _port_is_available(bridge_config.host, bridge_config.port):
        bridge_config.port = _find_open_port(bridge_config.host)

    host = create_remote_executor_host(
        provider=provider,
        system_prompt=system_prompt,
        profile=args.profile,
        bridge_config=bridge_config,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_args: stop_event.set())

    try:
        await host.start()
        print(json.dumps(_build_ready_payload(host.config)), flush=True)
        await stop_event.wait()
        return 0
    finally:
        await host.stop()


def main() -> None:
    logging.basicConfig(level=logging.ERROR)
    try:
        raise SystemExit(asyncio.run(_run()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
