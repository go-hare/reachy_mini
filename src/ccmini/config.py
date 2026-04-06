"""Configuration loading for ccmini.

Layered config: defaults < global (~/.ccmini/config.json) < project (.ccmini.json) < CLI flags.

Legacy compatibility:
- Reads and migrates ``~/.mini_agent/config.json`` if the new ccmini config
  does not exist yet.
- Falls back to ``.mini-agent.json`` when ``.ccmini.json`` is absent.

Extended features (ported from Claude Code's config patterns):
- **Remote config sync** — overlay from ``~/.ccmini/remote_config.json``
- **Config validation** — type-check all fields, report errors
- **Config hot reload** — watch config file and fire callbacks on change
- **Environment config** — ``MINI_AGENT_*`` env vars with full field mapping
- **Config profiles** — named presets in ``~/.ccmini/profiles/``
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .auth import get_provider_api_key
from .paths import mini_agent_home
logger = logging.getLogger(__name__)


def _home_dir() -> Path:
    return mini_agent_home()


def _global_config_path() -> Path:
    return _home_dir() / "config.json"


def _legacy_global_config_path() -> Path:
    return Path.home() / ".mini_agent" / "config.json"


def _project_config_path() -> Path:
    return Path.cwd() / ".ccmini.json"


def _legacy_project_config_path() -> Path:
    return Path.cwd() / ".mini-agent.json"


@dataclass
class CLIConfig:
    """Merged configuration used by the CLI."""

    provider: str = "anthropic"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    ccmini_host: str = "127.0.0.1"
    ccmini_port: int = 7779
    ccmini_auth_token: str = ""
    max_tokens: int = 8192
    max_turns: int = 50
    system_prompt: str = ""  # empty → use build_default_prompt()

    session_dir: str = ""
    session_persistence: bool = True

    buddy_enabled: bool = True
    buddy_user_id: str = ""

    tools_enabled: bool = True
    allowed_dirs: list[str] = field(default_factory=list)
    bash_timeout: int = 120

    theme: str = "auto"
    output_style: str = "markdown"
    multiline_key: str = "shift+enter"
    permission_mode: str = "default"
    permission_rules: list[dict[str, str]] = field(default_factory=list)
    statusline_enabled: bool = True
    coordinator_enabled: bool = False
    kairos_enabled: bool = False
    kairos_brief_enabled: bool = False
    kairos_cron_enabled: bool = True
    kairos_cron_durable: bool = True
    kairos_channels_enabled: bool = False
    kairos_dream_enabled: bool = True
    builtin_explore_plan_agents_enabled: bool = True
    builtin_verification_agent_enabled: bool = True
    builtin_statusline_guide_agent_enabled: bool = True
    builtin_claude_docs_guide_agent_enabled: bool = True
    verbose: bool = False

    def __post_init__(self) -> None:
        if not self.session_dir:
            self.session_dir = str(_home_dir() / "sessions")


def load_config(
    *,
    cli_overrides: dict[str, Any] | None = None,
    config_file: str | None = None,
) -> CLIConfig:
    """Load configuration from all layers, merging top-down.

    Priority: CLI flags > project config > global config > defaults.
    """
    cfg = CLIConfig()
    _ensure_global_config_exists(cfg)

    global_data = _load_json(_global_config_path())
    project_path = _project_config_path()
    if project_path.exists():
        project_data = _load_json(project_path)
    else:
        project_data = _load_json(_legacy_project_config_path())

    if config_file:
        explicit_data = _load_json(Path(config_file))
    else:
        explicit_data = {}

    merged = {**global_data, **project_data, **explicit_data}

    if cli_overrides:
        merged.update({k: v for k, v in cli_overrides.items() if v is not None and v != ""})

    valid_fields = {f.name for f in fields(CLIConfig)}
    for key, value in merged.items():
        if key in valid_fields:
            setattr(cfg, key, value)

    if not cfg.api_key:
        cfg.api_key = get_provider_api_key(cfg.provider)

    _apply_env_vars(cfg)
    return cfg


def save_global_config(updates: dict[str, Any]) -> Path:
    """Save updates to the global config file."""
    path = _global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = _load_json(path)
    existing.update(updates)

    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _ensure_global_config_exists(defaults: CLIConfig | None = None) -> Path:
    """Create the global config file with defaults if it is missing."""
    cfg = defaults or CLIConfig()
    path = _global_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path

    legacy_path = _legacy_global_config_path()
    if legacy_path.exists():
        path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
        return path

    data = {
        "provider": cfg.provider,
        "model": cfg.model,
        "api_key": cfg.api_key,
        "base_url": cfg.base_url,
        "ccmini_host": cfg.ccmini_host,
        "ccmini_port": cfg.ccmini_port,
        "ccmini_auth_token": cfg.ccmini_auth_token,
        "max_tokens": cfg.max_tokens,
        "max_turns": cfg.max_turns,
        "output_style": cfg.output_style,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _apply_env_vars(cfg: CLIConfig) -> None:
    """Override config from environment variables (MINI_AGENT_*)."""
    for attr, value in load_env_config().items():
        setattr(cfg, attr, value)


# ======================================================================
# Full environment variable → config field mapping
# ======================================================================

_ENV_VAR_MAP: dict[str, str] = {
    "MINI_AGENT_PROVIDER": "provider",
    "MINI_AGENT_MODEL": "model",
    "MINI_AGENT_API_KEY": "api_key",
    "MINI_AGENT_BASE_URL": "base_url",
    "CCMINI_HOST": "ccmini_host",
    "CCMINI_PORT": "ccmini_port",
    "CCMINI_AUTH_TOKEN": "ccmini_auth_token",
    "MINI_AGENT_MAX_TOKENS": "max_tokens",
    "MINI_AGENT_MAX_TURNS": "max_turns",
    "MINI_AGENT_SYSTEM_PROMPT": "system_prompt",
    "MINI_AGENT_SESSION_DIR": "session_dir",
    "MINI_AGENT_BASH_TIMEOUT": "bash_timeout",
    "MINI_AGENT_THEME": "theme",
    "MINI_AGENT_OUTPUT_STYLE": "output_style",
    "MINI_AGENT_MULTILINE_KEY": "multiline_key",
    "MINI_AGENT_PERMISSION_MODE": "permission_mode",
    "MINI_AGENT_STATUSLINE_ENABLED": "statusline_enabled",
    "MINI_AGENT_COORDINATOR_ENABLED": "coordinator_enabled",
    "MINI_AGENT_KAIROS_ENABLED": "kairos_enabled",
    "MINI_AGENT_KAIROS_BRIEF_ENABLED": "kairos_brief_enabled",
    "MINI_AGENT_KAIROS_CRON_ENABLED": "kairos_cron_enabled",
    "MINI_AGENT_KAIROS_CRON_DURABLE": "kairos_cron_durable",
    "MINI_AGENT_KAIROS_CHANNELS_ENABLED": "kairos_channels_enabled",
    "MINI_AGENT_KAIROS_DREAM_ENABLED": "kairos_dream_enabled",
    "MINI_AGENT_BUILTIN_EXPLORE_PLAN_AGENTS_ENABLED": "builtin_explore_plan_agents_enabled",
    "MINI_AGENT_BUILTIN_VERIFICATION_AGENT_ENABLED": "builtin_verification_agent_enabled",
    "MINI_AGENT_BUILTIN_STATUSLINE_GUIDE_AGENT_ENABLED": "builtin_statusline_guide_agent_enabled",
    "MINI_AGENT_BUILTIN_CLAUDE_DOCS_GUIDE_AGENT_ENABLED": "builtin_claude_docs_guide_agent_enabled",
    "MINI_AGENT_VERBOSE": "verbose",
    "MINI_AGENT_BUDDY_ENABLED": "buddy_enabled",
    "MINI_AGENT_TOOLS_ENABLED": "tools_enabled",
    "MINI_AGENT_SESSION_PERSISTENCE": "session_persistence",
}

_INT_FIELDS = {"max_tokens", "max_turns", "bash_timeout", "ccmini_port"}
_BOOL_FIELDS = {
    "verbose", "buddy_enabled", "tools_enabled", "session_persistence",
    "statusline_enabled", "coordinator_enabled", "kairos_enabled", "kairos_brief_enabled",
    "kairos_cron_enabled", "kairos_cron_durable", "kairos_channels_enabled",
    "kairos_dream_enabled", "builtin_explore_plan_agents_enabled",
    "builtin_verification_agent_enabled",
    "builtin_statusline_guide_agent_enabled",
    "builtin_claude_docs_guide_agent_enabled",
}


def load_env_config() -> dict[str, Any]:
    """Read all ``MINI_AGENT_*`` env vars and return as a config dict.

    Type coercion is applied: int fields are cast to int, bool fields
    accept ``"1"``, ``"true"``, ``"yes"`` (case-insensitive).
    """
    result: dict[str, Any] = {}
    for env_key, attr in _ENV_VAR_MAP.items():
        raw = os.environ.get(env_key, "")
        if not raw:
            continue
        if attr in _INT_FIELDS:
            try:
                result[attr] = int(raw)
            except ValueError:
                logger.warning("Ignoring non-integer env %s=%s", env_key, raw)
        elif attr in _BOOL_FIELDS:
            result[attr] = raw.lower() in ("1", "true", "yes")
        else:
            result[attr] = raw
    return result


# ======================================================================
# Config validation
# ======================================================================

@dataclass
class ValidationError:
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


def validate_config(cfg: CLIConfig) -> list[ValidationError]:
    """Validate a CLIConfig, returning a list of errors (empty = valid).

    Checks:
    - Required string fields are non-empty where sensible
    - Numeric fields are within sane ranges
    - Provider is a known value
    """
    errors: list[ValidationError] = []

    if not isinstance(cfg.provider, str) or not cfg.provider:
        errors.append(ValidationError("provider", "must be a non-empty string"))
    elif cfg.provider not in _KNOWN_PROVIDERS:
        errors.append(ValidationError(
            "provider",
            f"unknown provider '{cfg.provider}', expected one of {sorted(_KNOWN_PROVIDERS)}",
        ))

    if cfg.model and not isinstance(cfg.model, str):
        errors.append(ValidationError("model", "must be a string"))

    if cfg.ccmini_host and not isinstance(cfg.ccmini_host, str):
        errors.append(ValidationError("ccmini_host", "must be a string"))

    if not isinstance(cfg.ccmini_port, int) or cfg.ccmini_port < 1:
        errors.append(ValidationError("ccmini_port", "must be a positive integer"))

    if not isinstance(cfg.max_tokens, int) or cfg.max_tokens < 1:
        errors.append(ValidationError("max_tokens", "must be a positive integer"))
    elif cfg.max_tokens > 1_000_000:
        errors.append(ValidationError("max_tokens", "suspiciously large (>1M)"))

    if not isinstance(cfg.max_turns, int) or cfg.max_turns < 1:
        errors.append(ValidationError("max_turns", "must be a positive integer"))

    if not isinstance(cfg.bash_timeout, int) or cfg.bash_timeout < 0:
        errors.append(ValidationError("bash_timeout", "must be a non-negative integer"))

    if cfg.theme not in _known_themes():
        errors.append(ValidationError("theme", f"unknown theme '{cfg.theme}'"))

    if cfg.output_style not in _known_output_styles():
        errors.append(ValidationError("output_style", f"unknown output style '{cfg.output_style}'"))

    if cfg.multiline_key not in {"shift+enter", "alt+enter"}:
        errors.append(
            ValidationError(
                "multiline_key",
                f"unknown multiline key '{cfg.multiline_key}'",
            )
        )

    from .permissions import PermissionMode

    if cfg.permission_mode not in {mode.value for mode in PermissionMode}:
        errors.append(
            ValidationError(
                "permission_mode",
                f"unknown permission mode '{cfg.permission_mode}'",
            )
        )

    if cfg.allowed_dirs and not isinstance(cfg.allowed_dirs, list):
        errors.append(ValidationError("allowed_dirs", "must be a list of strings"))

    if not isinstance(cfg.permission_rules, list):
        errors.append(ValidationError("permission_rules", "must be a list"))
    else:
        from .permissions import PermissionDecision

        valid_decisions = {decision.value for decision in PermissionDecision}
        for index, rule in enumerate(cfg.permission_rules):
            if not isinstance(rule, dict):
                errors.append(
                    ValidationError("permission_rules", f"rule #{index + 1} must be an object")
                )
                continue
            pattern = rule.get("tool_pattern", "")
            decision = rule.get("decision", "")
            if not isinstance(pattern, str) or not pattern.strip():
                errors.append(
                    ValidationError("permission_rules", f"rule #{index + 1} needs a non-empty tool_pattern")
                )
            if decision not in valid_decisions:
                errors.append(
                    ValidationError(
                        "permission_rules",
                        f"rule #{index + 1} has invalid decision '{decision}'",
                    )
                )

    return errors


_KNOWN_PROVIDERS = {
    "anthropic",
    "openai",
    "compatible",
    "ollama",
    "vllm",
    "deepseek",
    "bedrock",
    "vertex",
    "custom",
    "mock",
}

# Valid values for ``CLIConfig.theme`` (besides ``auto``).
BUILTIN_THEME_NAMES: tuple[str, ...] = (
    "dark",
    "light",
    "monokai",
    "dracula",
    "solarized",
    "minimal",
)


def _known_themes() -> set[str]:
    """Return allowed theme config values (includes ``auto``)."""
    return {"auto", *BUILTIN_THEME_NAMES}


def _known_output_styles() -> set[str]:
    """Return the output-style names supported by the renderer."""
    from .output_styles import OutputStyle

    return {style.value for style in OutputStyle}


# ======================================================================
# Remote config sync
# ======================================================================

def _remote_config_path() -> Path:
    return _home_dir() / "remote_config.json"


def sync_remote_config(data: dict[str, Any] | None = None) -> Path:
    """Write remote config overlay to ``~/.ccmini/remote_config.json``.

    If *data* is None, the file is left unchanged (read-only check).
    Returns the path to the remote config file.
    """
    path = _remote_config_path()
    if data is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8",
        )
    return path


def get_remote_setting(key: str, default: Any = None) -> Any:
    """Get a single key from the remote config overlay, with fallback."""
    data = _load_json(_remote_config_path())
    return data.get(key, default)


def load_remote_config() -> dict[str, Any]:
    """Load the full remote config overlay."""
    return _load_json(_remote_config_path())


# ======================================================================
# Config hot reload — watch file for changes
# ======================================================================

class ConfigWatcher:
    """Watch ``~/.ccmini/config.json`` and fire callbacks on change.

    Uses polling (stat-based) for maximum cross-platform compatibility,
    similar to Claude Code's ``watchFile`` pattern in config.ts.
    """

    def __init__(self, interval: float = 2.0) -> None:
        self._interval = interval
        self._callbacks: list[Any] = []
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_mtime: float = 0.0

    def on_config_change(self, callback: Any) -> Any:
        """Register a callback fired when the config file changes.

        Returns an unsubscribe function.
        """
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass

        return unsubscribe

    def start(self) -> None:
        """Start watching in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self._last_mtime = self._get_mtime()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop watching."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1)
            self._thread = None

    def _get_mtime(self) -> float:
        try:
            return _global_config_path().stat().st_mtime
        except OSError:
            return 0.0

    def _poll_loop(self) -> None:
        while self._running:
            time.sleep(self._interval)
            mtime = self._get_mtime()
            if mtime != self._last_mtime and mtime != 0.0:
                self._last_mtime = mtime
                new_cfg = load_config()
                for cb in list(self._callbacks):
                    try:
                        cb(new_cfg)
                    except Exception:
                        logger.debug("Config change callback error", exc_info=True)


_global_watcher: ConfigWatcher | None = None


def watch_config(interval: float = 2.0) -> ConfigWatcher:
    """Start (or return existing) global config file watcher."""
    global _global_watcher
    if _global_watcher is None:
        _global_watcher = ConfigWatcher(interval=interval)
        _global_watcher.start()
    return _global_watcher


def on_config_change(callback: Any) -> Any:
    """Convenience: register a config-change listener (auto-starts watcher)."""
    watcher = watch_config()
    return watcher.on_config_change(callback)


# ======================================================================
# Config profiles — named presets
# ======================================================================

def _profiles_dir() -> Path:
    return _home_dir() / "profiles"


def list_profiles() -> list[str]:
    """List available config profile names."""
    d = _profiles_dir()
    if not d.is_dir():
        return []
    return sorted(
        p.stem for p in d.glob("*.json")
    )


def load_profile(name: str) -> CLIConfig:
    """Load a named config profile and return a merged CLIConfig.

    Profile data is merged on top of the default config (same priority
    as project config).
    """
    path = _profiles_dir() / f"{name}.json"
    profile_data = _load_json(path)
    if not profile_data:
        raise FileNotFoundError(f"Profile '{name}' not found at {path}")
    return load_config(cli_overrides=profile_data)


def save_profile(name: str, config: CLIConfig | dict[str, Any]) -> Path:
    """Save a config profile.

    Accepts either a CLIConfig or a raw dict.
    """
    d = _profiles_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.json"

    if isinstance(config, CLIConfig):
        data: dict[str, Any] = {}
        for f in fields(CLIConfig):
            data[f.name] = getattr(config, f.name)
    else:
        data = dict(config)

    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    return path


def delete_profile(name: str) -> bool:
    """Delete a named profile. Returns True if it existed."""
    path = _profiles_dir() / f"{name}.json"
    if path.exists():
        path.unlink()
        return True
    return False
