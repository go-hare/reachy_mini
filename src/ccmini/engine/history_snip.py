"""History snip feature gate shim.

The recovered reference tree calls into ``services/compact/snipCompact``
only behind ``feature('HISTORY_SNIP')``. That module is absent from the
reference source we are restoring against, so the effective behaviour of
this build is a no-op.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..messages import Message


@dataclass(slots=True)
class SnipConfig:
    enabled: bool = False
    max_context_tokens: int = 150_000
    keep_recent_messages: int = 10
    snip_threshold_ratio: float = 0.75
    min_messages_to_snip: int = 4
    insert_marker: bool = True


def snip_if_needed(
    messages: list[Message],
    config: SnipConfig | None = None,
) -> tuple[list[Message], int]:
    del config
    return list(messages), 0


def enhanced_snip(
    messages: list[Message],
    config: SnipConfig | None = None,
) -> tuple[list[Message], int]:
    return snip_if_needed(messages, config)


__all__ = [
    "SnipConfig",
    "enhanced_snip",
    "snip_if_needed",
]
