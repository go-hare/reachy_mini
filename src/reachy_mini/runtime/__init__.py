"""Resident runtime helpers for Reachy Mini."""

from typing import Any

from .config import (
    ModelConfig,
    ProfileRuntimeConfig,
    SpeechInputRuntimeConfig,
    SpeechRuntimeConfig,
    load_profile_runtime_config,
)
from .embodiment import EmbodimentCoordinator
from .moves import MovementManager
from .profile_loader import ProfileBundle, load_profile_bundle
from .project import AppProject, create_app_project, inspect_app_project
from .reply_audio import RuntimeReplyAudioService
from .speech_driver import SpeechDriver
from .surface_driver import SurfaceDriver

__all__ = [
    "AppProject",
    "EmbodimentCoordinator",
    "ModelConfig",
    "HostedAppProject",
    "MovementManager",
    "ProfileBundle",
    "ProfileRuntimeConfig",
    "RuntimeReplyAudioService",
    "SpeechInputRuntimeConfig",
    "SpeechDriver",
    "SpeechRuntimeConfig",
    "SurfaceDriver",
    "WebBinding",
    "build_web_host",
    "create_app_project",
    "inspect_app_project",
    "load_profile_bundle",
    "load_profile_runtime_config",
    "resolve_web_binding",
]


def __getattr__(name: str) -> Any:
    """Load web-host helpers lazily to avoid importing app/runtime stacks eagerly."""
    if name in {"HostedAppProject", "WebBinding", "build_web_host", "resolve_web_binding"}:
        from .web import (
            HostedAppProject,
            WebBinding,
            build_web_host,
            resolve_web_binding,
        )

        exports = {
            "HostedAppProject": HostedAppProject,
            "WebBinding": WebBinding,
            "build_web_host": build_web_host,
            "resolve_web_binding": resolve_web_binding,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
