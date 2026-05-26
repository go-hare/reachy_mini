"""Phase 2 absence guard: legacy modules are no longer importable."""

from __future__ import annotations

import importlib
import importlib.util

import pytest


def _module(*parts: str) -> str:
    return ".".join(parts)


@pytest.mark.parametrize(
    "module_name",
    [
        _module("reachy_mini", "front"),
        "reachy_mini.companion",
        _module("reachy_mini", "companion", "intent"),
        "reachy_mini.runtime.scheduler",
        "reachy_mini.runtime.speech_session",
        "reachy_mini.runtime.model_factory",
        "reachy_mini.core",
        "reachy_mini.core.agent",
        "reachy_mini.core.kernel",
        "reachy_mini.core.routing",
        "reachy_mini.core.resident",
        "reachy_mini.core.turns",
        "reachy_mini.core.memory",
        "reachy_mini.core.message_utils",
        "reachy_mini.core.tooling",
        "reachy_mini.core.run_store",
        "reachy_mini.core.sleep_agent",
        "reachy_mini.core.models",
        "reachy_mini.core._compat",
        "reachy_mini.apps.inputs",
    ],
)
def test_legacy_module_is_not_importable(module_name: str) -> None:
    """Phase 2 deletes the v3 front/kernel/scheduler stack."""
    try:
        spec = importlib.util.find_spec(module_name)
    except ModuleNotFoundError:
        # Parent package was already deleted; treat as absent.
        return
    if spec is None:
        return
    raise AssertionError(
        f"{module_name} still resolvable; spec={spec!r}; "
        f"origin={getattr(spec, 'origin', None)}; "
        f"locations={getattr(spec, 'submodule_search_locations', None)}"
    )


def test_top_level_exports_v4_surface() -> None:
    """The v4 SDK surface is reachable from the top-level package."""
    package = importlib.import_module("reachy_mini")
    for name in (
        "ReachyMini",
        "ReachyMiniApp",
        "RuntimeSession",
        "BrainAgent",
        "AgentConfig",
        "ActionExecutor",
        "ActionRegistry",
        "ActionSpec",
    ):
        assert getattr(package, name) is not None
