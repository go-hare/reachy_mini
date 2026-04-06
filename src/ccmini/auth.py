"""Persistent login state for mini-agent providers."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .paths import mini_agent_path


def _auth_path():
    return mini_agent_path("auth.json")


@dataclass(slots=True)
class ProviderAuth:
    """Stored authentication state for a single provider."""

    provider: str
    api_key: str
    account_label: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_auth_state() -> dict[str, ProviderAuth]:
    """Return the persisted provider auth map."""
    path = _auth_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    providers = raw.get("providers", {})
    if not isinstance(providers, dict):
        return {}
    result: dict[str, ProviderAuth] = {}
    for provider, payload in providers.items():
        if not isinstance(payload, dict):
            continue
        api_key = str(payload.get("api_key", "")).strip()
        if not api_key:
            continue
        result[provider] = ProviderAuth(
            provider=provider,
            api_key=api_key,
            account_label=str(payload.get("account_label", "")).strip(),
            updated_at=float(payload.get("updated_at", time.time())),
        )
    return result


def save_auth_state(entries: dict[str, ProviderAuth]) -> None:
    """Persist provider auth entries to disk."""
    path = _auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "providers": {
            provider: entry.to_dict()
            for provider, entry in sorted(entries.items())
        }
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def save_provider_auth(provider: str, api_key: str, *, account_label: str = "") -> ProviderAuth:
    """Create or replace auth state for a provider."""
    provider = provider.strip().lower()
    entry = ProviderAuth(
        provider=provider,
        api_key=api_key.strip(),
        account_label=account_label.strip(),
        updated_at=time.time(),
    )
    entries = load_auth_state()
    entries[provider] = entry
    save_auth_state(entries)
    return entry


def get_provider_auth(provider: str) -> ProviderAuth | None:
    """Return auth state for a provider, if present."""
    return load_auth_state().get(provider.strip().lower())


def get_provider_api_key(provider: str) -> str:
    """Return the stored api key for a provider, or an empty string."""
    entry = get_provider_auth(provider)
    return entry.api_key if entry is not None else ""


def clear_provider_auth(provider: str) -> bool:
    """Remove auth state for a provider."""
    provider = provider.strip().lower()
    entries = load_auth_state()
    if provider not in entries:
        return False
    entries.pop(provider, None)
    save_auth_state(entries)
    return True


def clear_all_auth() -> None:
    """Remove all stored provider auth state."""
    save_auth_state({})


def list_auth_providers() -> list[ProviderAuth]:
    """List stored provider auth entries."""
    return sorted(load_auth_state().values(), key=lambda item: item.provider)


def mask_secret(secret: str) -> str:
    """Return a human-friendly masked version of a secret."""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"
