"""Kairos core.

The recovered reference tree does not ship a standalone Kairos subsystem.
Only the brief / channels / cron / sleep surfaces map cleanly to concrete
source files. Proactive and dream behavior therefore stay disabled unless
explicitly forced on by the local runtime.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

from ..paths import mini_agent_path

# ---------------------------------------------------------------------------
# Feature flags — compile-time-equivalent gates
# ---------------------------------------------------------------------------

class Feature(str, Enum):
    """Build-time feature flags mirroring Claude Code's feature() calls."""
    KAIROS = "kairos"
    KAIROS_BRIEF = "kairos_brief"
    KAIROS_CHANNELS = "kairos_channels"
    KAIROS_DREAM = "kairos_dream"
    PROACTIVE = "proactive"
    AGENT_TRIGGERS = "agent_triggers"


_ENABLED_FEATURES: set[str] = set()
_features_lock = threading.Lock()


def enable_feature(feat: Feature | str) -> None:
    """Enable a feature flag at runtime."""
    val = feat.value if isinstance(feat, Feature) else feat.lower()
    with _features_lock:
        _ENABLED_FEATURES.add(val)


def disable_feature(feat: Feature | str) -> None:
    val = feat.value if isinstance(feat, Feature) else feat.lower()
    with _features_lock:
        _ENABLED_FEATURES.discard(val)


def feature(name: str) -> bool:
    """Check whether a feature flag is enabled (mirrors TS feature() calls)."""
    with _features_lock:
        return name.lower() in _ENABLED_FEATURES


def enabled_features() -> frozenset[str]:
    with _features_lock:
        return frozenset(_ENABLED_FEATURES)


# ---------------------------------------------------------------------------
# Runtime gate config (replaces GrowthBook)
# ---------------------------------------------------------------------------

@dataclass
class GateConfig:
    """Runtime gate values — replaces GrowthBook tengu_kairos_* flags.

    Loaded from environment variables or a JSON config file.
    """
    kairos_enabled: bool = False
    brief_enabled: bool = False
    proactive_enabled: bool = False
    cron_enabled: bool = True
    cron_durable: bool = True
    channels_enabled: bool = False
    dream_enabled: bool = False

    @classmethod
    def from_env(cls) -> GateConfig:
        def _bool(key: str, default: bool = False) -> bool:
            val = os.environ.get(key, "").lower()
            if val in ("1", "true", "yes", "on"):
                return True
            if val in ("0", "false", "no", "off"):
                return False
            return default

        return cls(
            kairos_enabled=_bool("MINI_KAIROS", False),
            brief_enabled=_bool("MINI_KAIROS_BRIEF", False),
            proactive_enabled=_bool("MINI_KAIROS_PROACTIVE", False),
            cron_enabled=_bool("MINI_KAIROS_CRON", True),
            cron_durable=_bool("MINI_KAIROS_CRON_DURABLE", True),
            channels_enabled=_bool("MINI_KAIROS_CHANNELS", False),
            dream_enabled=_bool("MINI_KAIROS_DREAM", False),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GateConfig:
        return cls(
            kairos_enabled=bool(data.get("kairos_enabled", False)),
            brief_enabled=bool(data.get("brief_enabled", False)),
            proactive_enabled=bool(data.get("proactive_enabled", False)),
            cron_enabled=bool(data.get("cron_enabled", True)),
            cron_durable=bool(data.get("cron_durable", True)),
            channels_enabled=bool(data.get("channels_enabled", False)),
            dream_enabled=bool(data.get("dream_enabled", False)),
        )


_gate_config = GateConfig()
_gate_lock = threading.Lock()


def set_gate_config(cfg: GateConfig) -> None:
    global _gate_config
    with _gate_lock:
        _gate_config = cfg


def get_gate_config() -> GateConfig:
    with _gate_lock:
        return _gate_config


# ---------------------------------------------------------------------------
# Kairos global state (mirrors bootstrap/state.ts kairosActive section)
# ---------------------------------------------------------------------------

class KairosMode(str, Enum):
    """Operating mode of the Kairos system."""
    INACTIVE = "inactive"
    ASSISTANT = "assistant"          # Full autonomous assistant
    PROACTIVE_ONLY = "proactive"     # Proactive ticks but no assistant persona
    CRON_ONLY = "cron"               # Only cron tasks, no proactive ticks


@dataclass
class KairosState:
    """Global mutable state for the Kairos subsystem."""
    active: bool = False
    mode: KairosMode = KairosMode.INACTIVE
    forced: bool = False             # --assistant flag bypasses gate
    trusted: bool = False            # Directory trust check passed

    context_blocked: bool = False    # API error → block ticks
    paused: bool = False             # User cancelled → pause proactive
    sleeping: bool = False           # Agent called Sleep

    tick_count: int = 0
    last_tick_ts: float = 0.0
    last_wake_ts: float = 0.0

    brief_enabled: bool = False
    user_msg_opt_in: bool = False    # User toggled /brief

    channels: list[str] = field(default_factory=list)
    allowed_channel_plugins: list[str] = field(default_factory=list)


_state = KairosState()
_state_lock = threading.Lock()


def get_kairos_state() -> KairosState:
    with _state_lock:
        return _state


def _mutate_state(**kwargs: Any) -> None:
    with _state_lock:
        for k, v in kwargs.items():
            if hasattr(_state, k):
                setattr(_state, k, v)


def set_kairos_active(value: bool) -> None:
    _mutate_state(active=value)


def is_kairos_active() -> bool:
    with _state_lock:
        return _state.active


def mark_assistant_forced() -> None:
    """--assistant flag: skip gate check."""
    _mutate_state(forced=True)


def is_assistant_forced() -> bool:
    with _state_lock:
        return _state.forced


# ---------------------------------------------------------------------------
# Activation — the main entry point called from agent startup
# ---------------------------------------------------------------------------

def activate_kairos(
    *,
    mode: KairosMode = KairosMode.ASSISTANT,
    trust_accepted: bool = True,
    gate_config: GateConfig | None = None,
) -> bool:
    """Attempt to activate Kairos. Returns True if activation succeeded.

    Mirrors main.tsx ~line 1064-1092 activation sequence:
    1. Check if forced (--assistant flag)
    2. Check directory trust
    3. Check gate config
    4. Enable appropriate feature flags
    5. Set state
    """
    cfg = gate_config or get_gate_config()

    if not is_assistant_forced() and not cfg.kairos_enabled:
        return False

    if not trust_accepted and not is_assistant_forced():
        return False

    # Enable feature flags based on config
    enable_feature(Feature.KAIROS)
    if cfg.proactive_enabled and mode in (KairosMode.ASSISTANT, KairosMode.PROACTIVE_ONLY):
        enable_feature(Feature.PROACTIVE)
    if cfg.brief_enabled:
        enable_feature(Feature.KAIROS_BRIEF)
    if cfg.channels_enabled:
        enable_feature(Feature.KAIROS_CHANNELS)
    if cfg.dream_enabled:
        enable_feature(Feature.KAIROS_DREAM)
    if cfg.cron_enabled:
        enable_feature(Feature.AGENT_TRIGGERS)

    _mutate_state(
        active=True,
        mode=mode,
        trusted=trust_accepted,
        brief_enabled=cfg.brief_enabled,
    )
    return True


def deactivate_kairos() -> None:
    """Shut down Kairos cleanly."""
    for feat in Feature:
        disable_feature(feat)
    _mutate_state(
        active=False,
        mode=KairosMode.INACTIVE,
        paused=False,
        sleeping=False,
        context_blocked=False,
    )


# ---------------------------------------------------------------------------
# Trust check
# ---------------------------------------------------------------------------

_TRUST_LEDGER_PATH = mini_agent_path("trusted_projects.json")


def _load_trust_ledger() -> set[str]:
    try:
        import json

        if not _TRUST_LEDGER_PATH.exists():
            return set()
        data = json.loads(_TRUST_LEDGER_PATH.read_text(encoding="utf-8"))
        projects = data.get("projects", [])
        if not isinstance(projects, list):
            return set()
        return {str(item).strip() for item in projects if str(item).strip()}
    except Exception:
        return set()


def _save_trust_ledger(projects: set[str]) -> None:
    import json

    _TRUST_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TRUST_LEDGER_PATH.write_text(
        json.dumps({"projects": sorted(projects)}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def remember_trusted_directory(cwd: str | Path | None = None) -> Path:
    root = Path(cwd).resolve() if cwd is not None else Path.cwd().resolve()
    projects = _load_trust_ledger()
    projects.add(str(root))
    _save_trust_ledger(projects)
    return _TRUST_LEDGER_PATH


def forget_trusted_directory(cwd: str | Path | None = None) -> Path:
    root = Path(cwd).resolve() if cwd is not None else Path.cwd().resolve()
    projects = _load_trust_ledger()
    projects.discard(str(root))
    _save_trust_ledger(projects)
    return _TRUST_LEDGER_PATH

def check_directory_trust(cwd: str | Path | None = None) -> bool:
    """Check if the working directory has been trusted by the user.

    Trust is accepted when either:
    - ``CCMINI_TRUST_ALL`` is enabled
    - the project contains ``.ccmini/trusted``
    - the project path is present in the global trusted-project ledger
    """
    if os.environ.get("CCMINI_TRUST_ALL", "").lower() in ("1", "true"):
        return True
    if cwd is None:
        cwd = Path.cwd()
    root = Path(cwd).resolve()
    trust_marker = root / ".ccmini" / "trusted"
    if trust_marker.exists():
        return True
    return str(root) in _load_trust_ledger()


# ---------------------------------------------------------------------------
# System prompt addendum for assistant mode
# ---------------------------------------------------------------------------

_ASSISTANT_PROMPT_ADDENDUM = """\
# Assistant Mode

You are running in assistant mode. You are a persistent, proactive AI assistant \
for this project. Key behaviors:

- You receive periodic <tick> prompts that keep you alive between turns.
- When there is nothing useful to do, call Sleep to conserve resources.
- When you wake from Sleep, check for pending tasks, cron jobs, and channel messages.
- Use SendUserMessage to communicate with the user (don't output text directly).
- You can schedule recurring tasks with CronCreate.
- External services may push messages via channels — handle them appropriately.
- Be proactive: if you notice something that needs attention, act on it.
- If the user is away, summarize what happened when they return.
"""


def get_assistant_system_prompt_addendum() -> str:
    """Return the assistant-mode system prompt section."""
    if not is_kairos_active():
        return ""
    return _ASSISTANT_PROMPT_ADDENDUM


# ---------------------------------------------------------------------------
# Feature gate — config-file-driven with env-var overrides
# ---------------------------------------------------------------------------

_KAIROS_CONFIG_PATH = mini_agent_path("kairos_config.json")

_FEATURE_GATE_NAMES = (
    "kairos_enabled",
    "proactive_enabled",
    "cron_enabled",
    "dream_enabled",
    "brief_enabled",
)


class FeatureGate:
    """Config-file + env-var feature gate system.

    Reads ``~/.ccmini/kairos_config.json`` on first access and caches.
    Individual gates can be overridden at runtime via env vars of the form
    ``KAIROS_FEATURE_<NAME>=1`` (case-insensitive).
    """

    def __init__(self, config_path: Path | str | None = None) -> None:
        self._path = Path(config_path) if config_path else _KAIROS_CONFIG_PATH
        self._cache: dict[str, bool] | None = None
        self._lock = threading.Lock()

    def _load(self) -> dict[str, bool]:
        if self._cache is not None:
            return self._cache
        data: dict[str, Any] = {}
        if self._path.exists():
            try:
                import json
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        self._cache = {name: bool(data.get(name, False)) for name in _FEATURE_GATE_NAMES}
        return self._cache

    def reload(self) -> None:
        with self._lock:
            self._cache = None
            self._load()

    def is_feature_enabled(self, name: str) -> bool:
        env_key = f"KAIROS_FEATURE_{name.upper()}"
        env_val = os.environ.get(env_key, "").lower()
        if env_val in ("1", "true", "yes", "on"):
            return True
        if env_val in ("0", "false", "no", "off"):
            return False
        with self._lock:
            return self._load().get(name.lower(), False)

    def set_feature(self, name: str, enabled: bool) -> None:
        with self._lock:
            cache = self._load()
            cache[name.lower()] = enabled

    def save(self) -> None:
        import json
        with self._lock:
            cache = self._load()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
            tmp.replace(self._path)


_feature_gate: FeatureGate | None = None


def get_feature_gate() -> FeatureGate:
    global _feature_gate
    if _feature_gate is None:
        _feature_gate = FeatureGate()
    return _feature_gate


def is_feature_enabled(name: str) -> bool:
    return get_feature_gate().is_feature_enabled(name)


# ---------------------------------------------------------------------------
# Runtime gate — multi-condition checks before enabling Kairos
# ---------------------------------------------------------------------------

@dataclass
class RuntimeGateResult:
    enabled: bool
    reasons: list[str] = field(default_factory=list)


class RuntimeGate:
    """Multi-condition pre-flight checks for Kairos activation."""

    @staticmethod
    def _is_ci() -> bool:
        ci_vars = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "TF_BUILD")
        return any(os.environ.get(v) for v in ci_vars)

    @staticmethod
    def _is_headless() -> bool:
        if os.environ.get("DISPLAY") is None and os.name != "nt":
            return True
        return os.environ.get("TERM", "").lower() == "dumb"

    @staticmethod
    def _has_disk_space(min_mb: int = 50) -> bool:
        try:
            import shutil
            usage = shutil.disk_usage(Path.home())
            return (usage.free / (1024 * 1024)) >= min_mb
        except OSError:
            return True

    @staticmethod
    def _is_subscription_active() -> bool:
        """Placeholder — always returns True until billing is wired."""
        return True

    @classmethod
    def evaluate_gates(cls) -> RuntimeGateResult:
        reasons: list[str] = []
        if cls._is_ci():
            reasons.append("running_in_ci")
        if cls._is_headless():
            reasons.append("headless_environment")
        if not cls._has_disk_space():
            reasons.append("insufficient_disk_space")
        if not cls._is_subscription_active():
            reasons.append("subscription_inactive")
        return RuntimeGateResult(enabled=len(reasons) == 0, reasons=reasons)


# ---------------------------------------------------------------------------
# Global state persistence
# ---------------------------------------------------------------------------

_KAIROS_STATE_PATH = mini_agent_path("kairos_state.json")


def persist_state(path: Path | str | None = None) -> None:
    """Save Kairos global state snapshot to disk."""
    import json
    target = Path(path) if path else _KAIROS_STATE_PATH
    with _state_lock:
        data = {
            "last_active_time": time.time(),
            "session_count": _state.tick_count,
            "total_ticks": _state.tick_count,
            "mode": _state.mode.value,
            "active": _state.active,
            "sleeping": _state.sleeping,
            "last_tick_ts": _state.last_tick_ts,
            "last_wake_ts": _state.last_wake_ts,
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(target)


def restore_state(path: Path | str | None = None) -> dict[str, Any]:
    """Restore Kairos global state from disk. Returns the loaded dict."""
    import json
    target = Path(path) if path else _KAIROS_STATE_PATH
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    _mutate_state(
        last_tick_ts=data.get("last_tick_ts", 0.0),
        last_wake_ts=data.get("last_wake_ts", 0.0),
        tick_count=data.get("total_ticks", 0),
    )
    return data


# ---------------------------------------------------------------------------
# Session history — track session start/end for dream consolidation
# ---------------------------------------------------------------------------

_KAIROS_SESSIONS_PATH = mini_agent_path("kairos_sessions.json")


@dataclass
class SessionRecord:
    session_id: str
    start: float
    end: float
    message_count: int = 0


class SessionHistory:
    """Persistent session history for dream consolidation and analytics."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else _KAIROS_SESSIONS_PATH
        self._lock = threading.Lock()
        self._sessions: list[SessionRecord] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        import json
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for entry in data.get("sessions", []):
                    self._sessions.append(SessionRecord(
                        session_id=entry["session_id"],
                        start=entry["start"],
                        end=entry["end"],
                        message_count=entry.get("message_count", 0),
                    ))
            except (json.JSONDecodeError, OSError, KeyError):
                pass
        self._loaded = True

    def _save(self) -> None:
        import json
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated_at": time.time(),
            "sessions": [
                {
                    "session_id": s.session_id,
                    "start": s.start,
                    "end": s.end,
                    "message_count": s.message_count,
                }
                for s in self._sessions
            ],
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def record_session(
        self,
        session_id: str,
        start: float,
        end: float,
        message_count: int = 0,
    ) -> None:
        with self._lock:
            self._ensure_loaded()
            self._sessions = [s for s in self._sessions if s.session_id != session_id]
            self._sessions.append(SessionRecord(
                session_id=session_id,
                start=start,
                end=end,
                message_count=message_count,
            ))
            self._save()

    def get_recent_sessions(self, count: int = 10) -> list[SessionRecord]:
        with self._lock:
            self._ensure_loaded()
            sorted_sessions = sorted(self._sessions, key=lambda s: s.end, reverse=True)
            return sorted_sessions[:count]

    def get_sessions_since(self, timestamp: float) -> list[SessionRecord]:
        with self._lock:
            self._ensure_loaded()
            return [s for s in self._sessions if s.end >= timestamp]

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._loaded = True
            self._save()
