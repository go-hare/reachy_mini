"""Networking helpers shared by bridge launchers and hosts."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterator


def client_host(host: str) -> str:
    """Return a connectable host for a bridge bind address."""
    normalized = host.strip()
    if normalized in {"", "0.0.0.0"}:
        return "127.0.0.1"
    if normalized == "::":
        return "::1"
    return normalized


def format_host_for_url(host: str) -> str:
    """Format a host for URL construction, adding IPv6 brackets when needed."""
    resolved = client_host(host)
    try:
        address = ipaddress.ip_address(resolved)
    except ValueError:
        return resolved
    if isinstance(address, ipaddress.IPv6Address):
        return f"[{resolved}]"
    return resolved


def build_connect_url(
    *,
    host: str,
    port: int,
    ssl: bool = False,
    websocket: bool = False,
) -> str:
    """Build a connectable HTTP or WebSocket bridge URL."""
    if websocket:
        scheme = "wss" if ssl else "ws"
    else:
        scheme = "https" if ssl else "http"
    return f"{scheme}://{format_host_for_url(host)}:{port}"


def iter_bind_endpoints(host: str, port: int) -> Iterator[tuple[int, int, int, tuple[object, ...]]]:
    """Yield socket bind endpoints for the requested host and port."""
    infos = socket.getaddrinfo(
        host,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        flags=socket.AI_PASSIVE,
    )
    seen: set[tuple[int, int, int, tuple[object, ...]]] = set()
    for family, socktype, proto, _canonname, sockaddr in infos:
        endpoint = (family, socktype, proto, tuple(sockaddr))
        if endpoint in seen:
            continue
        seen.add(endpoint)
        yield endpoint
