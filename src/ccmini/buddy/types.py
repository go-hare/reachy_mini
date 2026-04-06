"""Buddy core types aligned to the recovered reference source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


RARITIES = (
    "common",
    "uncommon",
    "rare",
    "epic",
    "legendary",
)
Rarity = Literal["common", "uncommon", "rare", "epic", "legendary"]


duck = "duck"
goose = "goose"
blob = "blob"
cat = "cat"
dragon = "dragon"
octopus = "octopus"
owl = "owl"
penguin = "penguin"
turtle = "turtle"
snail = "snail"
ghost = "ghost"
axolotl = "axolotl"
capybara = "capybara"
cactus = "cactus"
robot = "robot"
rabbit = "rabbit"
mushroom = "mushroom"
chonk = "chonk"

SPECIES = (
    duck,
    goose,
    blob,
    cat,
    dragon,
    octopus,
    owl,
    penguin,
    turtle,
    snail,
    ghost,
    axolotl,
    capybara,
    cactus,
    robot,
    rabbit,
    mushroom,
    chonk,
)
Species = Literal[
    "duck",
    "goose",
    "blob",
    "cat",
    "dragon",
    "octopus",
    "owl",
    "penguin",
    "turtle",
    "snail",
    "ghost",
    "axolotl",
    "capybara",
    "cactus",
    "robot",
    "rabbit",
    "mushroom",
    "chonk",
]


EYES = ("·", "✦", "×", "◉", "@", "°")
Eye = Literal["·", "✦", "×", "◉", "@", "°"]

HATS = (
    "none",
    "crown",
    "tophat",
    "propeller",
    "halo",
    "wizard",
    "beanie",
    "tinyduck",
)
Hat = Literal[
    "none",
    "crown",
    "tophat",
    "propeller",
    "halo",
    "wizard",
    "beanie",
    "tinyduck",
]

STAT_NAMES = (
    "DEBUGGING",
    "PATIENCE",
    "CHAOS",
    "WISDOM",
    "SNARK",
)
StatName = Literal["DEBUGGING", "PATIENCE", "CHAOS", "WISDOM", "SNARK"]


@dataclass(slots=True)
class CompanionBones:
    rarity: Rarity
    species: Species
    eye: Eye
    hat: Hat
    shiny: bool
    stats: dict[StatName, int]


@dataclass(slots=True)
class CompanionSoul:
    name: str
    personality: str


@dataclass(slots=True)
class Companion:
    rarity: Rarity
    species: Species
    eye: Eye
    hat: Hat
    shiny: bool
    stats: dict[StatName, int]
    name: str
    personality: str
    hatchedAt: int


@dataclass(slots=True)
class StoredCompanion:
    name: str
    personality: str
    hatchedAt: int


RARITY_WEIGHTS: dict[Rarity, int] = {
    "common": 60,
    "uncommon": 25,
    "rare": 10,
    "epic": 4,
    "legendary": 1,
}

RARITY_STARS: dict[Rarity, str] = {
    "common": "★",
    "uncommon": "★★",
    "rare": "★★★",
    "epic": "★★★★",
    "legendary": "★★★★★",
}

RARITY_COLORS: dict[Rarity, str] = {
    "common": "inactive",
    "uncommon": "success",
    "rare": "permission",
    "epic": "autoAccept",
    "legendary": "warning",
}


__all__ = [
    "EYES",
    "HATS",
    "RARITIES",
    "RARITY_COLORS",
    "RARITY_STARS",
    "RARITY_WEIGHTS",
    "SPECIES",
    "STAT_NAMES",
    "Companion",
    "CompanionBones",
    "CompanionSoul",
    "Eye",
    "Hat",
    "Rarity",
    "Species",
    "StatName",
    "StoredCompanion",
    "axolotl",
    "blob",
    "cactus",
    "capybara",
    "cat",
    "chonk",
    "dragon",
    "duck",
    "ghost",
    "goose",
    "mushroom",
    "octopus",
    "owl",
    "penguin",
    "rabbit",
    "robot",
    "snail",
    "turtle",
]
