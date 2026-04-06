"""Unified file persistence — JSON key-value store with TTL and atomic writes.

Ported from Claude Code's ``utils/filePersistence/filePersistence.ts``:
- ``FilePersistence`` — save / load / delete / list JSON data with optional TTL
- ``AtomicFileWriter`` — temp-file + rename pattern, handles Windows locking
- ``ConfigPersistence`` — specialised subclass with schema validation and migration

Storage location: ``~/.mini-agent/data/`` (one JSON file per key).
"""

from __future__ import annotations

import json
import logging
import os
import platform
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_FORMAT_VERSION = 1


# ── Helpers ─────────────────────────────────────────────────────────

def _data_dir() -> Path:
    from ..config import _home_dir
    return _home_dir() / "data"


def _key_to_path(base: Path, key: str) -> Path:
    safe_key = key.replace("/", "__").replace("\\", "__").replace("..", "_")
    return base / f"{safe_key}.json"


# ── Atomic file writer ──────────────────────────────────────────────

class AtomicFileWriter:
    """Write files atomically using temp-file + rename.

    On POSIX, ``os.replace`` is atomic. On Windows the rename can fail
    if the target is locked; we retry a few times with short sleeps.
    """

    _IS_WINDOWS = platform.system() == "Windows"

    @staticmethod
    def write(path: Path, content: str, *, encoding: str = "utf-8") -> None:
        """Atomically write *content* to *path*."""
        path.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding=encoding) as f:
                f.write(content)

            if AtomicFileWriter._IS_WINDOWS:
                AtomicFileWriter._windows_replace(tmp_path, str(path))
            else:
                os.replace(tmp_path, str(path))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def _windows_replace(src: str, dst: str, retries: int = 5) -> None:
        for attempt in range(retries):
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                if attempt == retries - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))

    @staticmethod
    def read(path: Path, *, encoding: str = "utf-8", retries: int = 3) -> str | None:
        """Read *path* with retry on lock errors (Windows)."""
        for attempt in range(retries):
            try:
                return path.read_text(encoding=encoding)
            except PermissionError:
                if attempt == retries - 1:
                    return None
                time.sleep(0.05 * (attempt + 1))
            except FileNotFoundError:
                return None
            except OSError:
                return None
        return None


# ── Envelope ────────────────────────────────────────────────────────

@dataclass(slots=True)
class _Envelope:
    """Metadata wrapper stored alongside the actual data."""

    version: int = _DATA_FORMAT_VERSION
    created: float = 0.0
    ttl: float = 0.0
    data: Any = None

    def is_expired(self) -> bool:
        if self.ttl <= 0:
            return False
        return time.time() > (self.created + self.ttl)


def _pack(data: Any, ttl: float) -> str:
    envelope = {
        "version": _DATA_FORMAT_VERSION,
        "created": time.time(),
        "ttl": ttl,
        "data": data,
    }
    return json.dumps(envelope, ensure_ascii=False, indent=2)


def _unpack(raw: str) -> _Envelope:
    obj = json.loads(raw)
    return _Envelope(
        version=obj.get("version", 1),
        created=obj.get("created", 0.0),
        ttl=obj.get("ttl", 0.0),
        data=obj.get("data"),
    )


# ── FilePersistence ─────────────────────────────────────────────────

class FilePersistence:
    """JSON key-value store backed by individual files.

    Each key maps to ``~/.mini-agent/data/<key>.json``.

    Usage::

        store = FilePersistence()
        store.save("my_key", {"some": "data"}, ttl=3600)
        data = store.load("my_key", default={})
        store.delete("my_key")
    """

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._base = Path(base_dir) if base_dir else _data_dir()

    @property
    def base_dir(self) -> Path:
        return self._base

    def save(self, key: str, data: Any, *, ttl: float = 0.0) -> Path:
        """Save JSON-serializable *data* under *key* with optional TTL (seconds)."""
        path = _key_to_path(self._base, key)
        content = _pack(data, ttl)
        AtomicFileWriter.write(path, content)
        logger.debug("Persisted key=%s path=%s ttl=%s", key, path, ttl)
        return path

    def load(self, key: str, *, default: Any = None) -> Any:
        """Load data for *key*. Returns *default* if missing or expired."""
        path = _key_to_path(self._base, key)
        raw = AtomicFileWriter.read(path)
        if raw is None:
            return default

        try:
            envelope = _unpack(raw)
        except (json.JSONDecodeError, KeyError):
            logger.debug("Corrupt persistence file for key=%s", key)
            return default

        if envelope.is_expired():
            logger.debug("Expired key=%s (created=%.0f ttl=%.0f)", key, envelope.created, envelope.ttl)
            self.delete(key)
            return default

        return envelope.data

    def delete(self, key: str) -> bool:
        """Delete persisted data for *key*. Returns True if it existed."""
        path = _key_to_path(self._base, key)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            logger.debug("Failed to delete key=%s: %s", key, exc)
            return False

    def list_keys(self, prefix: str = "") -> list[str]:
        """List all keys matching *prefix*."""
        if not self._base.is_dir():
            return []

        keys: list[str] = []
        safe_prefix = prefix.replace("/", "__").replace("\\", "__")
        for p in sorted(self._base.glob("*.json")):
            key = p.stem.replace("__", "/")
            if key.startswith(prefix) or p.stem.startswith(safe_prefix):
                keys.append(key)
        return keys

    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns count removed."""
        if not self._base.is_dir():
            return 0

        removed = 0
        for p in self._base.glob("*.json"):
            raw = AtomicFileWriter.read(p)
            if raw is None:
                continue
            try:
                envelope = _unpack(raw)
                if envelope.is_expired():
                    p.unlink()
                    removed += 1
            except Exception:
                pass

        if removed:
            logger.debug("Cleaned up %d expired persistence entries", removed)
        return removed


# ── ConfigPersistence ───────────────────────────────────────────────

class ConfigPersistence(FilePersistence):
    """Specialized persistence for configuration data.

    Adds:
    - Schema validation before save
    - Version-based migration support
    """

    def __init__(
        self,
        base_dir: Path | str | None = None,
        *,
        schema_validator: Any = None,
        migrations: dict[int, Any] | None = None,
    ) -> None:
        config_dir = Path(base_dir) if base_dir else _data_dir() / "config"
        super().__init__(config_dir)
        self._validator = schema_validator
        self._migrations = migrations or {}

    def save(self, key: str, data: Any, *, ttl: float = 0.0) -> Path:
        """Save config data, running schema validation first."""
        if self._validator is not None:
            errors = self._validator(data)
            if errors:
                raise ValueError(f"Config validation failed for '{key}': {errors}")
        return super().save(key, data, ttl=ttl)

    def load(self, key: str, *, default: Any = None) -> Any:
        """Load config data, applying migrations if the stored version is old."""
        path = _key_to_path(self._base, key)
        raw = AtomicFileWriter.read(path)
        if raw is None:
            return default

        try:
            envelope = _unpack(raw)
        except (json.JSONDecodeError, KeyError):
            return default

        if envelope.is_expired():
            self.delete(key)
            return default

        data = envelope.data
        stored_version = envelope.version

        if self._migrations and stored_version < _DATA_FORMAT_VERSION:
            data = self._apply_migrations(data, stored_version)
            self.save(key, data)

        return data

    def _apply_migrations(self, data: Any, from_version: int) -> Any:
        current = from_version
        while current < _DATA_FORMAT_VERSION:
            migrator = self._migrations.get(current)
            if migrator is not None:
                try:
                    data = migrator(data)
                except Exception:
                    logger.warning("Migration from v%d failed", current, exc_info=True)
                    break
            current += 1
        return data


# ── Module-level singleton ──────────────────────────────────────────

_default_store: FilePersistence | None = None


def get_persistence() -> FilePersistence:
    """Return (or create) the module-level default ``FilePersistence``."""
    global _default_store
    if _default_store is None:
        _default_store = FilePersistence()
    return _default_store
