"""Built-in v4 robot actions."""

from __future__ import annotations

from reachy_mini.action_runtime.registry import ActionRegistry

from . import look_at, nod, play_emotion, set_antenna, shake_head


def register_builtin_actions(registry: ActionRegistry) -> ActionRegistry:
    """Register the Phase 1 built-in robot actions."""
    registry.register(look_at.METADATA, look_at.build)
    registry.register(nod.METADATA, nod.build)
    registry.register(shake_head.METADATA, shake_head.build)
    registry.register(set_antenna.METADATA, set_antenna.build)
    registry.register(play_emotion.METADATA, play_emotion.build)
    return registry


def create_builtin_registry() -> ActionRegistry:
    """Create an action registry with all Phase 1 built-ins registered."""
    return register_builtin_actions(ActionRegistry())


__all__ = ["create_builtin_registry", "register_builtin_actions"]
