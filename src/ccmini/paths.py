"""Path helpers for ccmini persistent storage."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_HOME_DIRNAME = ".ccmini"
LEGACY_HOME_DIRNAME = ".mini_agent"


def mini_agent_home() -> Path:
    """Return the configured ccmini home directory."""
    override = os.environ.get("CCMINI_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    legacy_override = os.environ.get("MINI_AGENT_HOME", "").strip()
    if legacy_override:
        return Path(legacy_override).expanduser()
    return Path.home() / DEFAULT_HOME_DIRNAME


def mini_agent_path(*parts: str) -> Path:
    """Build a path under the configured mini-agent home directory."""
    return mini_agent_home().joinpath(*parts)
