"""Pure category inference. No I/O, no UI, no API dependencies.

Callers (the focus page and the migration CLI) pass in any history
they have. The function returns the recommended category given the
session's label, whether it is task-aligned, and what came before.
"""

from __future__ import annotations

from .enums import TimeLogCategory

_KEYWORDS: dict[TimeLogCategory, tuple[str, ...]] = {
    TimeLogCategory.WORKOUT: (
        "gym",
        "workout",
        "lift",
        "weights",
        "cardio",
        "run",
        "running",
        "jog",
        "swim",
        "swimming",
        "bike",
        "cycling",
        "yoga",
        "pilates",
        "hiit",
        "stretch",
        "exercise",
    ),
    TimeLogCategory.PLAY: (
        "game",
        "gaming",
        "zelda",
        "switch",
        "playstation",
        "ps5",
        "xbox",
        "steam",
        "movie",
        "movies",
        "show",
        "tv",
        "anime",
        "manga",
        "netflix",
        "youtube",
        "podcast",
        "book",
        "reading",
        "read",
        "music",
        "concert",
    ),
    TimeLogCategory.PERSONAL: (
        "cook",
        "cooking",
        "grocery",
        "groceries",
        "laundry",
        "clean",
        "cleaning",
        "chore",
        "chores",
        "rest",
        "nap",
        "sleep",
        "lunch",
        "dinner",
        "breakfast",
        "eat",
        "meal",
        "hangout",
        "hang out",
        "family",
        "friend",
        "friends",
        "errand",
        "errands",
        "shower",
    ),
}


def infer_category(
    label: str,
    task_id: str | None,
    history: list[tuple[str, TimeLogCategory]],
) -> TimeLogCategory | None:
    """Return the recommended category for a new session, or None if
    there is no confident signal.

    Precedence:
        1. ``task_id`` is set -> ``WORK``. The UI may override this;
           inference only suggests.
        2. Most-recent prior entry with the same ``label`` -> that
           category. The user teaches the system over time.
        3. Case-insensitive substring match against a keyword list
           (Play / Workout / Personal only).
        4. ``None`` when no signal matches. The focus page treats
           this as ``PERSONAL``; the migration CLI treats it as "skip
           this entry" to avoid re-classifying unclassifiable legacy
           sessions.

    ``history`` is expected to be ordered newest-first; the first
    match wins.
    """
    if task_id is not None:
        return TimeLogCategory.WORK

    for prior_label, prior_category in history:
        if prior_label == label and prior_category != TimeLogCategory.OTHER:
            return prior_category

    lowered = label.lower()
    if lowered:
        for category, words in _KEYWORDS.items():
            for word in words:
                if word in lowered:
                    return category

    return None
