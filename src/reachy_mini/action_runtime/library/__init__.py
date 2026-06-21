"""Built-in v4 robot actions."""

from __future__ import annotations

from reachy_mini.action_runtime.registry import ActionRegistry

from reachy_mini.runtime.live2d_avatar import Live2DCapabilities

from . import live2d, look_at, nod, play_emotion, set_antenna, shake_head


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


def create_live2d_registry(capabilities: Live2DCapabilities) -> ActionRegistry:
    """Create an action registry for native Live2D mode only."""
    registry = ActionRegistry()
    for metadata, builder in live2d.create_live2d_registry_actions(capabilities):
        registry.register(metadata, builder)
    return registry


__all__ = [
    "create_builtin_registry",
    "create_live2d_registry",
    "register_builtin_actions",
]
