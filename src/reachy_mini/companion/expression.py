"""Map companion intent to lightweight outer expression hints."""

from __future__ import annotations

from reachy_mini.companion.models import CompanionIntent, SurfaceExpression

_SURFACE_PRESETS: dict[str, SurfaceExpression] = {
    "comfort": SurfaceExpression(
        text_style="soft_wrap",
        expression="gentle_caring",
    ),
    "encourage": SurfaceExpression(
        text_style="bright_warm",
        expression="happy_gentle",
    ),
    "playful": SurfaceExpression(
        text_style="lively_warm",
        expression="playful_soft",
    ),
    "focused": SurfaceExpression(
        text_style="warm_clear",
        expression="attentive_warm",
    ),
    "quiet_company": SurfaceExpression(
        text_style="soft_calm",
        expression="soft_smile",
    ),
}


def build_surface_expression(
    intent: CompanionIntent,
    affect_state: object | None = None,
) -> SurfaceExpression:
    """Translate a companionship intent into lightweight style guidance."""

    _ = affect_state
    return _SURFACE_PRESETS.get(intent.mode, _SURFACE_PRESETS["quiet_company"])
