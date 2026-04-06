"""Keybinding system — configurable keyboard shortcuts.

Features:
- ``KeyBinding`` dataclass with combo, action, description, context
- ``KeybindingRegistry`` — register, unregister, resolve, persist
- Default bindings matching common terminal conventions
- Load / save from ``~/.mini-agent/keybindings.json``
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Data model ──────────────────────────────────────────────────────

@dataclass(slots=True)
class KeyBinding:
    """A single keyboard shortcut."""

    key_combo: str
    action: str
    description: str = ""
    context: str = ""


# ── Default bindings ────────────────────────────────────────────────

_DEFAULT_BINDINGS: list[KeyBinding] = [
    KeyBinding("Ctrl+C", "cancel", "Cancel current operation"),
    KeyBinding("Ctrl+D", "exit", "Exit the agent"),
    KeyBinding("Ctrl+Z", "undo", "Undo last file edit"),
    KeyBinding("Ctrl+R", "search_history", "Search command history"),
    KeyBinding("Escape Escape", "rewind", "Rewind / undo last turn"),
    KeyBinding("Shift+Enter", "multiline", "Multi-line input mode"),
    KeyBinding("Tab", "autocomplete", "Trigger autocomplete"),
    KeyBinding("Ctrl+L", "clear_screen", "Clear terminal screen"),
]

_KEYBINDINGS_SCHEMA = "https://www.schemastore.org/claude-code-keybindings.json"
_KEYBINDINGS_DOCS = "https://code.claude.com/docs/en/keybindings"


# ── Registry ────────────────────────────────────────────────────────

class KeybindingRegistry:
    """Manage keybindings with load/save support.

    The registry is initialised with the default bindings. User
    customisations (loaded from disk) are merged on top.

    Usage::

        reg = KeybindingRegistry()
        reg.load_from_config()  # merges user overrides
        action = reg.get_action("Ctrl+C")  # → "cancel"
    """

    def __init__(self) -> None:
        self._bindings: dict[str, KeyBinding] = {}
        for b in _DEFAULT_BINDINGS:
            self._bindings[_normalise(b.key_combo)] = b

    # ── Query ───────────────────────────────────────────────────────

    def get_action(self, key_combo: str) -> str | None:
        """Resolve a key combo to its action name, or None."""
        binding = self._bindings.get(_normalise(key_combo))
        return binding.action if binding else None

    def get_binding(self, key_combo: str) -> KeyBinding | None:
        return self._bindings.get(_normalise(key_combo))

    def list_bindings(self) -> list[KeyBinding]:
        """Return all registered bindings, sorted by combo."""
        return sorted(self._bindings.values(), key=lambda b: b.key_combo)

    def find_by_action(self, action: str) -> list[KeyBinding]:
        """Return all bindings for a given action."""
        return [b for b in self._bindings.values() if b.action == action]

    # ── Mutate ──────────────────────────────────────────────────────

    def register(
        self,
        key_combo: str,
        action: str,
        *,
        description: str = "",
        context: str = "",
    ) -> None:
        """Register (or overwrite) a keybinding."""
        norm = _normalise(key_combo)
        self._bindings[norm] = KeyBinding(
            key_combo=norm, action=action,
            description=description, context=context,
        )

    def unregister(self, key_combo: str) -> bool:
        """Remove a keybinding. Returns True if it existed."""
        return self._bindings.pop(_normalise(key_combo), None) is not None

    # ── Persistence ─────────────────────────────────────────────────

    def load_from_config(self, path: Path | str | None = None) -> int:
        """Load user keybinding overrides from JSON config.

        Returns the number of bindings loaded. Missing file is not an error.
        """
        p = Path(path) if path else _config_path()
        if not p.exists():
            return 0

        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load keybindings from %s: %s", p, exc)
            return 0

        entries = _coerce_bindings_payload(data)
        if entries is None:
            logger.warning("Keybindings config is not a bindings list/object: %s", p)
            return 0

        count = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            combo = entry.get("key_combo", "")
            action = entry.get("action", "")
            if combo and action:
                self.register(
                    combo, action,
                    description=entry.get("description", ""),
                    context=entry.get("context", ""),
                )
                count += 1

        logger.debug("Loaded %d keybindings from %s", count, p)
        return count

    def save_to_config(self, path: Path | str | None = None) -> Path:
        """Persist current keybindings to JSON config."""
        p = Path(path) if path else _config_path()
        p.parent.mkdir(parents=True, exist_ok=True)

        data = _build_keybindings_document(self.list_bindings())

        p.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        logger.debug("Saved %d keybindings to %s", len(data["bindings"]), p)
        return p

    def reset_defaults(self) -> None:
        """Reset all bindings to defaults, discarding customisations."""
        self._bindings.clear()
        for b in _DEFAULT_BINDINGS:
            self._bindings[_normalise(b.key_combo)] = b


# ── Normalisation ───────────────────────────────────────────────────

def _normalise(combo: str) -> str:
    """Normalise a key combo string for consistent lookup.

    Sorts modifiers alphabetically, joins with ``+``, and lowercases
    modifiers while preserving the key name case.

    Examples::

        _normalise("Ctrl+Shift+A") → "ctrl+shift+A"
        _normalise("shift+ctrl+a") → "ctrl+shift+a"
        _normalise("Escape Escape") → "Escape Escape"
    """
    if " " in combo and "+" not in combo.replace(" +", "").replace("+ ", ""):
        return combo.strip()

    parts = [p.strip() for p in combo.split("+")]
    if len(parts) <= 1:
        return combo.strip()

    modifiers = sorted(p.lower() for p in parts[:-1])
    key = parts[-1]
    return "+".join(modifiers + [key])


# ── Config path ─────────────────────────────────────────────────────

def _config_path() -> Path:
    from .config import _home_dir
    return _home_dir() / "keybindings.json"


def get_keybindings_path() -> Path:
    """Public accessor for the user keybindings file path."""
    return _config_path()


def generate_keybindings_template() -> str:
    """Generate a JSON template similar to Claude Code's keybindings file."""
    document = _build_keybindings_document(_DEFAULT_BINDINGS)
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _binding_to_dict(binding: KeyBinding) -> dict[str, str]:
    return {
        "key_combo": binding.key_combo,
        "action": binding.action,
        "description": binding.description,
        "context": binding.context,
    }


def _build_keybindings_document(bindings: list[KeyBinding]) -> dict[str, Any]:
    return {
        "$schema": _KEYBINDINGS_SCHEMA,
        "$docs": _KEYBINDINGS_DOCS,
        "bindings": [_binding_to_dict(binding) for binding in bindings],
    }


def _coerce_bindings_payload(data: Any) -> list[dict[str, Any]] | None:
    """Accept both legacy list files and Claude-style wrapper objects."""
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]

    if isinstance(data, dict):
        bindings = data.get("bindings")
        if isinstance(bindings, list):
            return [entry for entry in bindings if isinstance(entry, dict)]

    return None


# ── Module-level singleton ──────────────────────────────────────────

_registry: KeybindingRegistry | None = None


def get_registry() -> KeybindingRegistry:
    """Return (or create) the module-level keybinding registry."""
    global _registry
    if _registry is None:
        _registry = KeybindingRegistry()
        _registry.load_from_config()
    return _registry
