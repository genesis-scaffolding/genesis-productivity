"""Durable bootstrap state for the onboarding wizard.

One JSON file per user holds the state of the first-run wizard:
where the user is, whether the bootstrap is finished, and the
unsaved choices made along the way.

The module is a leaf: standard library plus pydantic, no
``genesis.configs``, no ``genesis.ui``, no DAV client. The state
file path is passed in by the caller. That keeps the state machine
unit-testable without Streamlit and without a server (ADR-011).

Two rules make the state safe to read from the app's nav path:

- A missing file reads as ``skipped`` (terminal). The file is
  written when the account is created, so a missing file means the
  account predates the wizard. Such an account is never interrupted.
- A corrupt file reads as ``skipped`` (terminal) too, with a
  warning logged. A broken state file must never lock a user out
  of the app.

See ADR-032.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

STATE_FILENAME = "onboarding.json"
"""Name of the per-user state file inside the user's state directory."""


class OnboardingStatus(StrEnum):
    """Lifecycle of the bootstrap.

    ``completed`` and ``skipped`` are terminal: the app does not
    ask again. They are kept apart so the Settings page can report
    what happened.
    """

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"

    @property
    def is_terminal(self) -> bool:
        """True for the statuses that end the bootstrap."""
        return self in (OnboardingStatus.COMPLETED, OnboardingStatus.SKIPPED)


class OnboardingStep(StrEnum):
    """The wizard's steps.

    The wire values are the lowercase enum names. They are the ids
    stored in ``current_step``.
    """

    PURPOSE = "purpose"
    PROFILE = "profile"
    PROJECTS = "projects"
    TASKS = "tasks"
    JOURNALS = "journals"
    CONTACTS = "contacts"


STEP_ORDER: tuple[OnboardingStep, ...] = (
    OnboardingStep.PURPOSE,
    OnboardingStep.PROFILE,
    OnboardingStep.PROJECTS,
    OnboardingStep.TASKS,
    OnboardingStep.JOURNALS,
    OnboardingStep.CONTACTS,
)
"""The order the steps are walked in."""


class OnboardingState(BaseModel):
    """The durable state of one account's bootstrap.

    Attributes:
        status: Where the bootstrap stands.
        current_step: Step id the wizard resumes at, or None.
        started_at: When the wizard was first opened.
        updated_at: When the state last changed.
        completed_at: When the wizard was completed.
        skipped_at: When the wizard was skipped.
        attempts: How many times the wizard has been started.
    """

    status: OnboardingStatus = OnboardingStatus.SKIPPED
    current_step: str | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    skipped_at: datetime | None = None
    attempts: int = 0

    model_config = {"extra": "ignore"}

    @property
    def is_terminal(self) -> bool:
        """True when the bootstrap is finished (completed or skipped)."""
        return self.status.is_terminal

    @property
    def never_run(self) -> bool:
        """True when the account was never offered the wizard.

        A missing state file reads as a skipped state with no
        ``skipped_at``. That is the "this account predates the
        wizard" verdict.
        """
        return self.status is OnboardingStatus.SKIPPED and self.skipped_at is None


def read_state(path: Path) -> OnboardingState:
    """Return the state stored at ``path``.

    A missing file, an unreadable file, or a file that does not
    parse all read as the terminal "predates the wizard" state.
    The unreadable cases log a warning; the missing case does not,
    because a missing file is the normal state for an account that
    was created before the wizard existed.
    """
    path = Path(path)
    if not path.exists():
        return OnboardingState()
    try:
        raw = path.read_text()
    except OSError as exc:
        logger.warning("Could not read the onboarding state at %s: %s", path, exc)
        return OnboardingState()
    try:
        return OnboardingState.model_validate_json(raw)
    except ValueError as exc:
        logger.warning("Could not parse the onboarding state at %s: %s", path, exc)
        return OnboardingState()


def write_state(path: Path, state: OnboardingState) -> None:
    """Write ``state`` to ``path`` atomically, mode 0o600.

    A temp file in the same directory is written first and then
    moved over the target with ``os.replace``, so a reader never
    sees a half-written file. Mirrors the users table's write
    pattern.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".onboarding.", suffix=".json.tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(state.model_dump_json(indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        # Best-effort cleanup; os.replace is atomic so the real
        # file is untouched on failure.
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def now_utc() -> datetime:
    """Return the current time as a UTC datetime."""
    return datetime.now(UTC)


__all__ = [
    "STATE_FILENAME",
    "STEP_ORDER",
    "OnboardingState",
    "OnboardingStatus",
    "OnboardingStep",
    "now_utc",
    "read_state",
    "write_state",
]
