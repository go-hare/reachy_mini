"""Sprite definitions — ASCII art for all 18 companion species.

Ported from Claude Code's ``buddy/sprites.ts``.

Each sprite is 5 lines tall, 12 chars wide (after ``{E}`` → eye substitution).
Every species has 3 animation frames for idle fidget animation.
Line 0 is the hat slot (blank in frames 0-1; frame 2 may use it for smoke etc).

Hat overlays replace line 0 when the companion has a hat and line 0 is blank.
"""

from __future__ import annotations

from typing import Any

from .types import CompanionBones, Eye, Hat, Species

# ── Sprite bodies ────────────────────────────────────────────────────
# {E} is replaced with the companion's eye character at render time.

BODIES: dict[str, list[list[str]]] = {
    "duck": [
        [
            "            ",
            "    __      ",
            "  <({E} )___  ",
            "   (  ._>   ",
            "    `--´    ",
        ],
        [
            "            ",
            "    __      ",
            "  <({E} )___  ",
            "   (  ._>   ",
            "    `--´~   ",
        ],
        [
            "            ",
            "    __      ",
            "  <({E} )___  ",
            "   (  .__>  ",
            "    `--´    ",
        ],
    ],
    "goose": [
        [
            "            ",
            "     ({E}>    ",
            "     ||     ",
            "   _(__)_   ",
            "    ^^^^    ",
        ],
        [
            "            ",
            "    ({E}>     ",
            "     ||     ",
            "   _(__)_   ",
            "    ^^^^    ",
        ],
        [
            "            ",
            "     ({E}>>   ",
            "     ||     ",
            "   _(__)_   ",
            "    ^^^^    ",
        ],
    ],
    "blob": [
        [
            "            ",
            "   .----.   ",
            "  ( {E}  {E} )  ",
            "  (      )  ",
            "   `----´   ",
        ],
        [
            "            ",
            "  .------.  ",
            " (  {E}  {E}  ) ",
            " (        ) ",
            "  `------´  ",
        ],
        [
            "            ",
            "    .--.    ",
            "   ({E}  {E})   ",
            "   (    )   ",
            "    `--´    ",
        ],
    ],
    "cat": [
        [
            "            ",
            "   /\\_/\\    ",
            "  ( {E}   {E})  ",
            "  (  ω  )   ",
            '  (")_(")   ',
        ],
        [
            "            ",
            "   /\\_/\\    ",
            "  ( {E}   {E})  ",
            "  (  ω  )   ",
            '  (")_(")~  ',
        ],
        [
            "            ",
            "   /\\-/\\    ",
            "  ( {E}   {E})  ",
            "  (  ω  )   ",
            '  (")_(")   ',
        ],
    ],
    "dragon": [
        [
            "            ",
            "  /^\\  /^\\  ",
            " <  {E}  {E}  > ",
            " (   ~~   ) ",
            "  `-vvvv-´  ",
        ],
        [
            "            ",
            "  /^\\  /^\\  ",
            " <  {E}  {E}  > ",
            " (        ) ",
            "  `-vvvv-´  ",
        ],
        [
            "   ~    ~   ",
            "  /^\\  /^\\  ",
            " <  {E}  {E}  > ",
            " (   ~~   ) ",
            "  `-vvvv-´  ",
        ],
    ],
    "octopus": [
        [
            "            ",
            "   .----.   ",
            "  ( {E}  {E} )  ",
            "  (______)  ",
            "  /\\/\\/\\/\\  ",
        ],
        [
            "            ",
            "   .----.   ",
            "  ( {E}  {E} )  ",
            "  (______)  ",
            "  \\/\\/\\/\\/  ",
        ],
        [
            "     o      ",
            "   .----.   ",
            "  ( {E}  {E} )  ",
            "  (______)  ",
            "  /\\/\\/\\/\\  ",
        ],
    ],
    "owl": [
        [
            "            ",
            "   /\\  /\\   ",
            "  (({E})({E}))  ",
            "  (  ><  )  ",
            "   `----´   ",
        ],
        [
            "            ",
            "   /\\  /\\   ",
            "  (({E})({E}))  ",
            "  (  ><  )  ",
            "   .----.   ",
        ],
        [
            "            ",
            "   /\\  /\\   ",
            "  (({E})(-))  ",
            "  (  ><  )  ",
            "   `----´   ",
        ],
    ],
    "penguin": [
        [
            "            ",
            "  .---.     ",
            "  ({E}>{E})     ",
            " /(   )\\    ",
            "  `---´     ",
        ],
        [
            "            ",
            "  .---.     ",
            "  ({E}>{E})     ",
            " |(   )|    ",
            "  `---´     ",
        ],
        [
            "  .---.     ",
            "  ({E}>{E})     ",
            " /(   )\\    ",
            "  `---´     ",
            "   ~ ~      ",
        ],
    ],
    "turtle": [
        [
            "            ",
            "   _,--._   ",
            "  ( {E}  {E} )  ",
            " /[______]\\ ",
            "  ``    ``  ",
        ],
        [
            "            ",
            "   _,--._   ",
            "  ( {E}  {E} )  ",
            " /[______]\\ ",
            "   ``  ``   ",
        ],
        [
            "            ",
            "   _,--._   ",
            "  ( {E}  {E} )  ",
            " /[======]\\ ",
            "  ``    ``  ",
        ],
    ],
    "snail": [
        [
            "            ",
            " {E}    .--.  ",
            "  \\  ( @ )  ",
            "   \\_`--´   ",
            "  ~~~~~~~   ",
        ],
        [
            "            ",
            "  {E}   .--.  ",
            "  |  ( @ )  ",
            "   \\_`--´   ",
            "  ~~~~~~~   ",
        ],
        [
            "            ",
            " {E}    .--.  ",
            "  \\  ( @  ) ",
            "   \\_`--´   ",
            "   ~~~~~~   ",
        ],
    ],
    "ghost": [
        [
            "            ",
            "   .----.   ",
            "  / {E}  {E} \\  ",
            "  |      |  ",
            "  ~`~``~`~  ",
        ],
        [
            "            ",
            "   .----.   ",
            "  / {E}  {E} \\  ",
            "  |      |  ",
            "  `~`~~`~`  ",
        ],
        [
            "    ~  ~    ",
            "   .----.   ",
            "  / {E}  {E} \\  ",
            "  |      |  ",
            "  ~~`~~`~~  ",
        ],
    ],
    "axolotl": [
        [
            "            ",
            "}~(______)~{",
            "}~({E} .. {E})~{",
            "  ( .--. )  ",
            "  (_/  \\_)  ",
        ],
        [
            "            ",
            "~}(______){~",
            "~}({E} .. {E}){~",
            "  ( .--. )  ",
            "  (_/  \\_)  ",
        ],
        [
            "            ",
            "}~(______)~{",
            "}~({E} .. {E})~{",
            "  (  --  )  ",
            "  ~_/  \\_~  ",
        ],
    ],
    "capybara": [
        [
            "            ",
            "  n______n  ",
            " ( {E}    {E} ) ",
            " (   oo   ) ",
            "  `------´  ",
        ],
        [
            "            ",
            "  n______n  ",
            " ( {E}    {E} ) ",
            " (   Oo   ) ",
            "  `------´  ",
        ],
        [
            "    ~  ~    ",
            "  u______n  ",
            " ( {E}    {E} ) ",
            " (   oo   ) ",
            "  `------´  ",
        ],
    ],
    "cactus": [
        [
            "            ",
            " n  ____  n ",
            " | |{E}  {E}| | ",
            " |_|    |_| ",
            "   |    |   ",
        ],
        [
            "            ",
            "    ____    ",
            " n |{E}  {E}| n ",
            " |_|    |_| ",
            "   |    |   ",
        ],
        [
            " n        n ",
            " |  ____  | ",
            " | |{E}  {E}| | ",
            " |_|    |_| ",
            "   |    |   ",
        ],
    ],
    "robot": [
        [
            "            ",
            "   .[||].   ",
            "  [ {E}  {E} ]  ",
            "  [ ==== ]  ",
            "  `------´  ",
        ],
        [
            "            ",
            "   .[||].   ",
            "  [ {E}  {E} ]  ",
            "  [ -==- ]  ",
            "  `------´  ",
        ],
        [
            "     *      ",
            "   .[||].   ",
            "  [ {E}  {E} ]  ",
            "  [ ==== ]  ",
            "  `------´  ",
        ],
    ],
    "rabbit": [
        [
            "            ",
            "   (\\__/)   ",
            "  ( {E}  {E} )  ",
            " =(  ..  )= ",
            '  (")__(")  ',
        ],
        [
            "            ",
            "   (|__/)   ",
            "  ( {E}  {E} )  ",
            " =(  ..  )= ",
            '  (")__(")  ',
        ],
        [
            "            ",
            "   (\\__/)   ",
            "  ( {E}  {E} )  ",
            " =( .  . )= ",
            '  (")__(")  ',
        ],
    ],
    "mushroom": [
        [
            "            ",
            " .-o-OO-o-. ",
            "(__________)",
            "   |{E}  {E}|   ",
            "   |____|   ",
        ],
        [
            "            ",
            " .-O-oo-O-. ",
            "(__________)",
            "   |{E}  {E}|   ",
            "   |____|   ",
        ],
        [
            "   . o  .   ",
            " .-o-OO-o-. ",
            "(__________)",
            "   |{E}  {E}|   ",
            "   |____|   ",
        ],
    ],
    "chonk": [
        [
            "            ",
            "  /\\    /\\  ",
            " ( {E}    {E} ) ",
            " (   ..   ) ",
            "  `------´  ",
        ],
        [
            "            ",
            "  /\\    /|  ",
            " ( {E}    {E} ) ",
            " (   ..   ) ",
            "  `------´  ",
        ],
        [
            "            ",
            "  /\\    /\\  ",
            " ( {E}    {E} ) ",
            " (   ..   ) ",
            "  `------´~ ",
        ],
    ],
}

# ── Hat overlays ─────────────────────────────────────────────────────

HAT_LINES: dict[str, str] = {
    "none": "",
    "crown": "   \\^^^/    ",
    "tophat": "   [___]    ",
    "propeller": "    -+-     ",
    "halo": "   (   )    ",
    "wizard": "    /^\\     ",
    "beanie": "   (___)    ",
    "tinyduck": "    ,>      ",
}


# ── Render functions ─────────────────────────────────────────────────


def render_sprite(bones: CompanionBones, frame: int = 0) -> list[str]:
    """Render a sprite frame with eye and hat substitution.

    Returns a list of strings (one per line). Hat line replaces line 0
    only when the species frame leaves it blank.
    """
    frames = BODIES.get(bones.species, BODIES["blob"])
    body = frames[frame % len(frames)]
    lines = [line.replace("{E}", bones.eye) for line in body]

    if bones.hat != "none" and not lines[0].strip():
        hat_line = HAT_LINES.get(bones.hat, "")
        if hat_line:
            lines[0] = hat_line

    # Drop blank hat slot if ALL frames have blank line 0
    if not lines[0].strip() and all(
        not f[0].strip() for f in frames
    ):
        lines = lines[1:]

    return lines


def sprite_frame_count(species: str) -> int:
    """Number of animation frames for a species."""
    frames = BODIES.get(species, BODIES["blob"])
    return len(frames)


def render_face(bones: CompanionBones) -> str:
    """Render a compact one-line face for narrow terminals."""
    e = bones.eye
    faces: dict[str, str] = {
        "duck": f"({e}>",
        "goose": f"({e}>",
        "blob": f"({e}{e})",
        "cat": f"={e}ω{e}=",
        "dragon": f"<{e}~{e}>",
        "octopus": f"~({e}{e})~",
        "owl": f"({e})({e})",
        "penguin": f"({e}>)",
        "turtle": f"[{e}_{e}]",
        "snail": f"{e}(@)",
        "ghost": f"/{e}{e}\\",
        "axolotl": f"}}{e}.{e}{{",
        "capybara": f"({e}oo{e})",
        "cactus": f"|{e}  {e}|",
        "robot": f"[{e}{e}]",
        "rabbit": f"({e}..{e})",
        "mushroom": f"|{e}  {e}|",
        "chonk": f"({e}.{e})",
    }
    return faces.get(bones.species, f"({e}{e})")
