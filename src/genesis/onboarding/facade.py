"""Facade for the onboarding wizard's durable state.

One facade per user. It owns the state file and is the only entry
point the rest of the app uses: the UI asks it whether the
bootstrap is still pending, the Settings page asks it to restart,
and the account-creation hook asks it to initialise.

The facade knows nothing about Streamlit or about the data the
wizard creates. It only knows about the wizard's own state. See
ADR-032.
"""

from __future__ import annotations

from pathlib import Path

from .state import (
    STATE_FILENAME,
    STEP_ORDER,
    OnboardingState,
    OnboardingStatus,
    OnboardingStep,
    now_utc,
    read_state,
    write_state,
)


class OnboardingFacade:
    """Read and write one account's onboarding state.

    Args:
        state_path: Path of the JSON state file. Normally
            ``<state_home>/genesis/<user>/onboarding.json``.
    """

    def __init__(self, state_path: Path) -> None:
        self._state_path = Path(state_path)

    @property
    def state_path(self) -> Path:
        """The state file this facade operates on."""
        return self._state_path

    # ----- Reads ---------------------------------------------------------

    def state(self) -> OnboardingState:
        """Return the current state.

        A missing or unreadable file reads as the terminal
        "predates the wizard" state.
        """
        return read_state(self._state_path)

    def is_pending(self) -> bool:
        """True while the bootstrap is still outstanding.

        This is the single gate the app asks before it decides to
        suppress the navigation.
        """
        return not self.state().is_terminal

    # ----- Writes --------------------------------------------------------

    def initialise(self) -> OnboardingState:
        """Give a brand-new account its state file.

        Writes the ``not_started`` state. An existing file is left
        alone: the account already has a state and overwriting it
        would reset a bootstrap in progress.
        """
        if self._state_path.exists():
            return self.state()
        now = now_utc()
        state = OnboardingState(
            status=OnboardingStatus.NOT_STARTED,
            started_at=now,
            updated_at=now,
        )
        write_state(self._state_path, state)
        return state

    def start(self, step: OnboardingStep = STEP_ORDER[0]) -> OnboardingState:
        """Open the wizard at ``step``.

        Moves a ``not_started`` account to ``in_progress``, stamps
        ``started_at`` the first time, and counts the attempt.
        Calling it on an account that is already running does not
        count a second attempt.
        """
        state = self.state()
        now = now_utc()
        if state.status is OnboardingStatus.NOT_STARTED:
            state.attempts += 1
            if state.started_at is None:
                state.started_at = now
        state.status = OnboardingStatus.IN_PROGRESS
        state.current_step = step.value
        state.updated_at = now
        write_state(self._state_path, state)
        return state

    def set_step(self, step: OnboardingStep) -> OnboardingState:
        """Record the step the wizard resumes at."""
        state = self.state()
        state.current_step = step.value
        state.updated_at = now_utc()
        write_state(self._state_path, state)
        return state

    def complete(self) -> OnboardingState:
        """Mark the bootstrap as completed."""
        state = self.state()
        now = now_utc()
        state.status = OnboardingStatus.COMPLETED
        state.completed_at = now
        state.current_step = None
        state.updated_at = now
        write_state(self._state_path, state)
        return state

    def skip(self) -> OnboardingState:
        """Mark the bootstrap as skipped.

        Skipping is terminal in the same way completing is: the
        system does not ask again.
        """
        state = self.state()
        now = now_utc()
        state.status = OnboardingStatus.SKIPPED
        state.skipped_at = now
        state.current_step = None
        state.updated_at = now
        write_state(self._state_path, state)
        return state

    def restart(self) -> OnboardingState:
        """Start the wizard again from a terminal state.

        Moves the status back to ``in_progress``, resets the step
        to the first one, clears the completion timestamps, and
        counts the attempt. The state file is rewritten, not
        deleted, so the attempt counter and the timestamps survive.

        Only the onboarding state is touched. The account's data is
        left exactly as it is.
        """
        state = self.state()
        now = now_utc()
        state.status = OnboardingStatus.IN_PROGRESS
        state.current_step = STEP_ORDER[0].value
        state.completed_at = None
        state.skipped_at = None
        state.started_at = now
        state.updated_at = now
        state.attempts += 1
        write_state(self._state_path, state)
        return state


__all__ = ["STATE_FILENAME", "STEP_ORDER", "OnboardingFacade", "OnboardingState", "OnboardingStatus", "OnboardingStep"]
