"""Pure builders that turn a session event into an ntfy payload.

Each builder is a plain ``(title, message, tags) -> tuple`` factory.
The builders do NOT touch the network; the caller (focus page or
focus create dialog) builds the tuple and hands it to
``NotifyFacade.send``. Keeping the builders pure means a future
FastAPI consumer can reuse them with no Streamlit dependency.

The builders accept an optional ``task_title`` so the caller can
disambiguate aligned sessions. Looking the title up via
``caldav.get_task(session.task_id).title`` is cheap but not free,
so the caller passes it in rather than letting the builder reach
into CalDAV. ADR-027.
"""

from __future__ import annotations

from ..entities import TimeLogEntry


def _label_for_session(session: TimeLogEntry, *, task_title: str | None) -> str:
    """Build the human-readable label for a session.

    Aligned sessions render as ``"Task: <task_title>"`` when the
    caller supplied a title. Unaligned sessions and aligned
    sessions without a resolved title render as
    ``"session.label"``. The label is what shows up in the ntfy
    notification body so the focus page does not have to
    disambiguate task-aligned vs unaligned at the call site.
    """
    if task_title:
        return f"Task: {task_title}"
    return session.label


def session_started(session: TimeLogEntry, *, task_title: str | None = None) -> tuple[str, str, list[str]]:
    """Build the payload for a session-started notification.

    Fires when the user clicks Start in the focus create dialog.
    The session has just been persisted; the label is what shows
    up on the active session card.
    """
    label = _label_for_session(session, task_title=task_title)
    return ("Focus session started", label, ["hourglass"])


def session_milestone(
    session: TimeLogEntry,
    minutes_elapsed: int,
    *,
    task_title: str | None = None,
) -> tuple[str, str, list[str]]:
    """Build the payload for a 30-minute milestone notification.

    ``minutes_elapsed`` is the threshold that was just crossed
    (30, 60, 90, ...). The body includes the value so a user
    scanning their notification history can tell which milestone
    fired without opening the page.
    """
    label = _label_for_session(session, task_title=task_title)
    return ("Focus milestone", f"{label} · {minutes_elapsed} min", ["hourglass"])


def session_finished(session: TimeLogEntry, *, task_title: str | None = None) -> tuple[str, str, list[str]]:
    """Build the payload for a countdown-finished notification.

    Fires when a countdown auto-stops at its ``due``. The body
    is just the label so the user can locate the session in the
    Statistics page without further detail.
    """
    label = _label_for_session(session, task_title=task_title)
    return ("Focus session finished", label, ["check"])
