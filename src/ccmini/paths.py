"""Path helpers for ccmini persistent storage."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_HOME_DIRNAME = ".ccmini"


def mini_agent_home() -> Path:
    """Return the configured ccmini home directory."""
    override = os.environ.get("CCMINI_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_HOME_DIRNAME


def mini_agent_path(*parts: str) -> Path:
    """Build a path under the configured ccmini home directory."""
    return mini_agent_home().joinpath(*parts)
