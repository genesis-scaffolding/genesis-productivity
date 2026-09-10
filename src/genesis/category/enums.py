"""Closed-set categories for time log entries."""

from __future__ import annotations

from enum import StrEnum


class TimeLogCategory(StrEnum):
    """Coarse tag for what kind of time a session represents.

    Values are wire-stable: they appear in iCal as ``X-CATEGORY``
    and in the API as JSON strings. Adding a new category is a
    breaking change to both surfaces.
    """

    WORK = "work"
    PLAY = "play"
    WORKOUT = "workout"
    PERSONAL = "personal"
    OTHER = "other"
