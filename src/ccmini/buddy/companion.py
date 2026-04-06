"""Buddy companion core aligned to the recovered reference source."""

from __future__ import annotations

import json
import math
from pathlib import Path

from ..paths import mini_agent_home
from .types import (
    EYES,
    HATS,
    RARITIES,
    RARITY_WEIGHTS,
    SPECIES,
    STAT_NAMES,
    Companion,
    CompanionBones,
    Rarity,
    StatName,
    StoredCompanion,
)

SALT = "friend-2026-401"


def _config_path() -> Path:
    return mini_agent_home() / "config.json"


def _read_global_config() -> dict[str, object]:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _imul(left: int, right: int) -> int:
    return ((left & 0xFFFFFFFF) * (right & 0xFFFFFFFF)) & 0xFFFFFFFF


def mulberry32(seed: int):
    a = seed & 0xFFFFFFFF

    def _next() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = _imul(a ^ (a >> 15), 1 | a)
        t = (t + _imul(t ^ (t >> 7), 61 | t)) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return _next


def hash_string(value: str) -> int:
    h = 2166136261
    for ch in value:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _pick(rng, values):
    return values[int(math.floor(rng() * len(values)))]


def _roll_rarity(rng) -> Rarity:
    total = sum(RARITY_WEIGHTS.values())
    roll = rng() * total
    for rarity in RARITIES:
        roll -= RARITY_WEIGHTS[rarity]
        if roll < 0:
            return rarity
    return "common"


RARITY_FLOOR: dict[Rarity, int] = {
    "common": 5,
    "uncommon": 15,
    "rare": 25,
    "epic": 35,
    "legendary": 50,
}


def _roll_stats(rng, rarity: Rarity) -> dict[StatName, int]:
    floor = RARITY_FLOOR[rarity]
    peak = _pick(rng, STAT_NAMES)
    dump = _pick(rng, STAT_NAMES)
    while dump == peak:
        dump = _pick(rng, STAT_NAMES)

    stats: dict[StatName, int] = {}
    for name in STAT_NAMES:
        if name == peak:
            stats[name] = min(100, floor + 50 + int(math.floor(rng() * 30)))
        elif name == dump:
            stats[name] = max(1, floor - 10 + int(math.floor(rng() * 15)))
        else:
            stats[name] = floor + int(math.floor(rng() * 40))
    return stats


def _roll_from(rng) -> dict[str, object]:
    rarity = _roll_rarity(rng)
    bones = CompanionBones(
        rarity=rarity,
        species=_pick(rng, SPECIES),
        eye=_pick(rng, EYES),
        hat="none" if rarity == "common" else _pick(rng, HATS),
        shiny=rng() < 0.01,
        stats=_roll_stats(rng, rarity),
    )
    return {
        "bones": bones,
        "inspirationSeed": int(math.floor(rng() * 1e9)),
    }


_roll_cache: dict[str, dict[str, object]] = {}


def clear_roll_cache() -> None:
    """Invalidate deterministic roll cache (e.g. after hatch writes new companion soul)."""
    _roll_cache.clear()


def roll(user_id: str) -> dict[str, object]:
    key = user_id + SALT
    cached = _roll_cache.get(key)
    if cached is not None:
        return cached
    value = _roll_from(mulberry32(hash_string(key)))
    _roll_cache[key] = value
    return value


def roll_with_seed(seed: str) -> dict[str, object]:
    return _roll_from(mulberry32(hash_string(seed)))


def companion_user_id() -> str:
    config = _read_global_config()
    oauth_account = config.get("oauthAccount")
    if isinstance(oauth_account, dict):
        account_uuid = oauth_account.get("accountUuid")
        if isinstance(account_uuid, str) and account_uuid.strip():
            return account_uuid
    user_id = config.get("userID")
    if isinstance(user_id, str) and user_id.strip():
        return user_id
    return "anon"


def get_companion(user_id: str | None = None) -> Companion | None:
    """Return hatched companion, or ``None``. Bones use ``roll(user_id or companion_user_id())``."""
    stored = _read_global_config().get("companion")
    if not isinstance(stored, dict):
        return None
    try:
        soul = StoredCompanion(
            name=str(stored["name"]),
            personality=str(stored["personality"]),
            hatchedAt=int(stored["hatchedAt"]),
        )
    except Exception:
        return None
    uid = user_id if user_id else companion_user_id()
    bones = roll(uid)["bones"]
    assert isinstance(bones, CompanionBones)
    return Companion(
        rarity=bones.rarity,
        species=bones.species,
        eye=bones.eye,
        hat=bones.hat,
        shiny=bones.shiny,
        stats=bones.stats,
        name=soul.name,
        personality=soul.personality,
        hatchedAt=soul.hatchedAt,
    )


def hatch_companion(
    user_id: str,
    *,
    name: str | None = None,
    personality: str | None = None,
) -> Companion:
    """Persist a new soul and return the live :class:`Companion` (cf. TS hatch flow)."""
    import random
    import time as _time

    from ..config import save_global_config

    clear_roll_cache()
    bones_obj = roll(user_id)["bones"]
    assert isinstance(bones_obj, CompanionBones)
    soul_name = (name or "").strip() or f"{bones_obj.species}-{random.randint(100, 999)}"
    pers = (personality or "").strip() or "curious and supportive"
    hatched = int(_time.time() * 1000)
    save_global_config(
        {
            "companion": {
                "name": soul_name,
                "personality": pers,
                "hatchedAt": hatched,
            }
        }
    )
    clear_roll_cache()
    result = get_companion(user_id)
    assert result is not None
    return result


__all__ = [
    "SALT",
    "clear_roll_cache",
    "companion_user_id",
    "get_companion",
    "hatch_companion",
    "hash_string",
    "mulberry32",
    "roll",
    "roll_with_seed",
]
