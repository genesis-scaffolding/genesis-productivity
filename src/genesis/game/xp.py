"""XP curve + per-event XP formulas (ADR-026).

Two pieces:

- ``XP_CURVE``: per-level XP required to reach the next level.
  Identical across all stats. From the Real-Life-Persona-System
  design note.
- Per-event formulas: how many XP a task completion or session
  stop awards / docks, and which stat.

All values are hardcoded constants. Changing them is a code
release.
"""

from __future__ import annotations

from typing import Final

from ..category.enums import TimeLogCategory

# Cumulative XP at the start of each level. Index 0 = Level 1 (0 XP),
# index 1 = Level 2 (30 XP cumulative), etc. Up to Level 10.
# To reach Level N, the user needs ``XP_CURVE[N]`` cumulative XP.
XP_CURVE: Final[list[int]] = [
    0,  # Level 1: 0 cumulative
    30,  # Level 2: 30 cumulative
    80,  # Level 3: 80 cumulative (50 more)
    150,  # Level 4: 150 (70 more)
    250,  # Level 5: 250 (100 more)
    390,  # Level 6: 390 (140 more)
    580,  # Level 7: 580 (190 more)
    830,  # Level 8: 830 (250 more)
    1150,  # Level 9: 1150 (320 more)
    1550,  # Level 10: 1550 (400 more)
]

MAX_LEVEL: Final[int] = len(XP_CURVE)


# XP awarded per event type.
TASK_COMPLETION_XP: Final[int] = 5

# Session reward: 1 XP per SESSION_XP_DIVISOR minutes, rounded down.
SESSION_XP_DIVISOR: Final[int] = 25

# PLAY penalty: 1 XP LOSS per PLAY_LOSS_DIVISOR minutes, rounded down.
PLAY_LOSS_DIVISOR: Final[int] = 120


def level_for_xp(xp: int) -> int:
    """Return the current level (1..MAX_LEVEL) for a cumulative XP total.

    Negative XP clamps to Level 1.
    """
    if xp <= 0:
        return 1
    level = 1
    for threshold in XP_CURVE[1:]:
        if xp >= threshold:
            level += 1
        else:
            break
    return min(level, MAX_LEVEL)


def xp_for_next_level(level: int) -> int | None:
    """Return the cumulative XP needed to reach ``level + 1``.

    Returns ``None`` if ``level`` is already at MAX_LEVEL (no
    further progression).
    """
    if level >= MAX_LEVEL:
        return None
    return XP_CURVE[level]


def xp_to_next_level(level: int, current_xp: int) -> int | None:
    """Return the XP delta from ``current_xp`` to the next level threshold.

    Returns ``None`` if at MAX_LEVEL.
    """
    target = xp_for_next_level(level)
    if target is None:
        return None
    return max(0, target - current_xp)


def stat_from_session_category(category: TimeLogCategory) -> str | None:
    """Return the tier-1 stat mapping for a session category.

    Returns ``None`` for PERSONAL / OTHER (caller falls through to
    keyword inference). PLAY is included so the caller can apply
    the negative delta formula in ``delta_from_session_duration``.
    """
    mapping = {
        TimeLogCategory.WORK: "discipline",
        TimeLogCategory.WORKOUT: "vitality",
        TimeLogCategory.PLAY: "discipline",
    }
    return mapping.get(category)


def delta_from_session_duration(duration_minutes: int, category: TimeLogCategory) -> int:
    """Return the signed XP delta for a session of ``duration_minutes``.

    - WORK / WORKOUT / PERSONAL / OTHER: ``floor(duration / 25)`` gain.
    - PLAY: ``-floor(duration / 120)`` loss (penalty).
    - Sub-minute durations yield 0 for gain categories, 0 for PLAY.
    - Negative durations are treated as 0 (shouldn't happen in
      practice but we don't want a sign flip).
    """
    safe_minutes = max(0, int(duration_minutes))
    if category == TimeLogCategory.PLAY:
        return -(safe_minutes // PLAY_LOSS_DIVISOR)
    return safe_minutes // SESSION_XP_DIVISOR


__all__ = [
    "MAX_LEVEL",
    "PLAY_LOSS_DIVISOR",
    "SESSION_XP_DIVISOR",
    "TASK_COMPLETION_XP",
    "XP_CURVE",
    "delta_from_session_duration",
    "level_for_xp",
    "stat_from_session_category",
    "xp_for_next_level",
    "xp_to_next_level",
]
