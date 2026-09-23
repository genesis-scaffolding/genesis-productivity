"""Tagged union for the source of an XP event (ADR-026).

The main ``Facade.record_xp_event`` constructs one of these from a
``task_id``, ``session_id``, or ad-hoc ``reason``. The game facade
then takes the source as a single typed argument and never has to
talk to the caldav module to fetch tasks or sessions.

This is the single contract between the orchestrating facade and
the game facade. New source kinds would extend this file only.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..category.enums import TimeLogCategory


@dataclass(frozen=True)
class TaskSource:
    """An XP event sourced from a task completion."""

    task_uid: str
    title: str
    description: str | None


@dataclass(frozen=True)
class SessionSource:
    """An XP event sourced from a session stop."""

    session_uid: str
    label: str
    category: TimeLogCategory


@dataclass(frozen=True)
class ReasonSource:
    """An XP event sourced from an ad-hoc reason (manual reward or penalty)."""

    reason: str


# Type alias for type-checkers; runtime dispatch is by isinstance.
XpSource = TaskSource | SessionSource | ReasonSource


__all__ = ["ReasonSource", "SessionSource", "TaskSource", "XpSource"]
