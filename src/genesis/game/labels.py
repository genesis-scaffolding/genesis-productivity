"""Level labels per stat (ADR-026).

Ten labels per stat, from Level 1 to Level 10. Sourced from the
Real-Life-Persona-System design note. Hardcoded — not user-editable.
Changing the labels is a code release (and a migration if we ever
want to preserve old character-state rows).

The labels are persona-flavor; the level numbers are what drive
behavior. Most users will never see most of these.
"""

from __future__ import annotations

from typing import Final

LABELS: Final[dict[str, list[str]]] = {
    "courage": [
        "Cowardly",
        "Hesitant",
        "Cautious",
        "Responsive",
        "Brave",
        "Bold",
        "Valiant",
        "Heroic",
        "Intrepid",
        "Legendary",
    ],
    "discipline": [
        "Chaotic",
        "Erratic",
        "Inconsistent",
        "Steady",
        "Consistent",
        "Disciplined",
        "Focused",
        "Masterful",
        "Exemplary",
        "Legendary",
    ],
    "charisma": [
        "Withdrawn",
        "Awkward",
        "Approaching",
        "Personable",
        "Charming",
        "Magnetic",
        "Captivating",
        "Influential",
        "Iconic",
        "Legendary",
    ],
    "knowledge": [
        "Unaware",
        "Curious",
        "Learning",
        "Informed",
        "Educated",
        "Learned",
        "Scholarly",
        "Erudite",
        "Sage",
        "Legendary",
    ],
    "vitality": [
        "Fragile",
        "Weakening",
        "Recovering",
        "Stable",
        "Healthy",
        "Robust",
        "Thriving",
        "Resilient",
        "Unstoppable",
        "Legendary",
    ],
}


def label_for_level(stat_id: str, level: int) -> str:
    """Return the label for ``stat_id`` at ``level`` (1..10).

    Out-of-range levels clamp to the nearest endpoint. Unknown
    ``stat_id`` returns the level number formatted as a string —
    the game facade should never call it with an unknown id, but
    we don't crash if config drifts.
    """
    ladder = LABELS.get(stat_id)
    if ladder is None:
        return f"L{level}"
    idx = max(0, min(len(ladder) - 1, level - 1))
    return ladder[idx]


__all__ = ["LABELS", "label_for_level"]
