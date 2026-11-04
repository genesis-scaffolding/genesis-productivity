"""Unit tests for the onboarding state machine.

No Streamlit, no server, no DAV client. The state module is a leaf
(standard library plus pydantic), so the whole state machine is
exercisable here. ADR-032.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from genesis.onboarding import (
    STATE_FILENAME,
    STEP_ORDER,
    OnboardingFacade,
    OnboardingState,
    OnboardingStatus,
    OnboardingStep,
    read_state,
    write_state,
)


@pytest.fixture
def facade(tmp_path: Path) -> OnboardingFacade:
    """Return a facade bound to a temp state file."""
    return OnboardingFacade(tmp_path / "genesis" / "alice" / STATE_FILENAME)


# ---------------------------------------------------------------------------
# Step order
# ---------------------------------------------------------------------------


def test_step_order_is_the_agreed_order() -> None:
    assert STEP_ORDER == (
        OnboardingStep.PURPOSE,
        OnboardingStep.PROFILE,
        OnboardingStep.LIFE_STORY,
        OnboardingStep.PROJECTS,
        OnboardingStep.TASKS,
        OnboardingStep.JOURNALS,
        OnboardingStep.CONTACTS,
    )


def test_purpose_is_the_first_step() -> None:
    assert STEP_ORDER[0] is OnboardingStep.PURPOSE


def test_step_order_has_seven_steps() -> None:
    assert len(STEP_ORDER) == 7


# ---------------------------------------------------------------------------
# Reading: missing and corrupt files
# ---------------------------------------------------------------------------


def test_missing_file_reads_as_terminal(tmp_path: Path) -> None:
    path = tmp_path / "nope.json"
    state = read_state(path)
    assert state.status is OnboardingStatus.SKIPPED
    assert state.is_terminal is True
    assert state.never_run is True


def test_missing_file_is_not_pending(tmp_path: Path) -> None:
    facade = OnboardingFacade(tmp_path / "nope.json")
    assert facade.is_pending() is False


def test_corrupt_file_reads_as_terminal(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json at all")
    with caplog.at_level(logging.WARNING):
        state = read_state(path)
    assert state.status is OnboardingStatus.SKIPPED
    assert state.is_terminal is True
    assert any("onboarding state" in record.getMessage() for record in caplog.records)


def test_corrupt_file_does_not_lock_the_user_out(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("nonsense")
    facade = OnboardingFacade(path)
    assert facade.is_pending() is False


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = OnboardingState(
        status=OnboardingStatus.IN_PROGRESS,
        current_step=OnboardingStep.PROJECTS.value,
        attempts=2,
        draft={"projects": ["Personal", "Learning"]},
    )
    write_state(path, state)
    reloaded = read_state(path)
    assert reloaded.status is OnboardingStatus.IN_PROGRESS
    assert reloaded.current_step == OnboardingStep.PROJECTS.value
    assert reloaded.attempts == 2
    assert reloaded.draft["projects"] == ["Personal", "Learning"]


def test_written_file_is_private(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    write_state(path, OnboardingState())
    assert oct(os.stat(path).st_mode)[-3:] == "600"


# ---------------------------------------------------------------------------
# Facade: initialise
# ---------------------------------------------------------------------------


def test_initialise_writes_not_started(facade: OnboardingFacade) -> None:
    state = facade.initialise()
    assert state.status is OnboardingStatus.NOT_STARTED
    assert state.started_at is not None
    assert facade.state().status is OnboardingStatus.NOT_STARTED


def test_initialise_makes_the_account_pending(facade: OnboardingFacade) -> None:
    facade.initialise()
    assert facade.is_pending() is True


def test_initialise_does_not_overwrite_an_existing_file(facade: OnboardingFacade) -> None:
    facade.initialise()
    facade.start()
    facade.set_step(OnboardingStep.TASKS)
    facade.initialise()
    state = facade.state()
    assert state.status is OnboardingStatus.IN_PROGRESS
    assert state.current_step == OnboardingStep.TASKS.value


# ---------------------------------------------------------------------------
# Facade: start, step, draft
# ---------------------------------------------------------------------------


def test_start_from_not_started_opens_the_wizard(facade: OnboardingFacade) -> None:
    facade.initialise()
    state = facade.start()
    assert state.status is OnboardingStatus.IN_PROGRESS
    assert state.current_step == OnboardingStep.PURPOSE.value
    assert state.attempts == 1
    assert state.started_at is not None


def test_set_step_round_trips(facade: OnboardingFacade) -> None:
    facade.initialise()
    facade.start()
    facade.set_step(OnboardingStep.CONTACTS)
    assert facade.state().current_step == OnboardingStep.CONTACTS.value


def test_set_draft_round_trips(facade: OnboardingFacade) -> None:
    facade.initialise()
    facade.set_draft("projects", ["Personal"])
    assert facade.state().draft["projects"] == ["Personal"]
    facade.set_draft("project_names", ["Personal", "Learning"])
    state = facade.state()
    assert state.draft["projects"] == ["Personal"]
    assert state.draft["project_names"] == ["Personal", "Learning"]


# ---------------------------------------------------------------------------
# Facade: terminal states
# ---------------------------------------------------------------------------


def test_complete_is_terminal(facade: OnboardingFacade) -> None:
    facade.initialise()
    facade.start()
    state = facade.complete()
    assert state.status is OnboardingStatus.COMPLETED
    assert state.completed_at is not None
    assert state.current_step is None
    assert facade.is_pending() is False


def test_skip_is_terminal(facade: OnboardingFacade) -> None:
    facade.initialise()
    facade.start()
    state = facade.skip()
    assert state.status is OnboardingStatus.SKIPPED
    assert state.skipped_at is not None
    assert state.current_step is None
    assert facade.is_pending() is False


def test_skipped_is_not_never_run(facade: OnboardingFacade) -> None:
    facade.initialise()
    facade.skip()
    assert facade.state().never_run is False


# ---------------------------------------------------------------------------
# Facade: restart
# ---------------------------------------------------------------------------


def test_restart_returns_to_the_first_step(facade: OnboardingFacade) -> None:
    facade.initialise()
    facade.start()
    facade.set_step(OnboardingStep.JOURNALS)
    facade.complete()
    state = facade.restart()
    assert state.status is OnboardingStatus.IN_PROGRESS
    assert state.current_step == OnboardingStep.PURPOSE.value
    assert facade.is_pending() is True


def test_restart_bumps_the_attempt_counter(facade: OnboardingFacade) -> None:
    facade.initialise()
    facade.start()
    before = facade.state().attempts
    facade.restart()
    assert facade.state().attempts == before + 1


def test_restart_clears_the_draft_and_the_timestamps(facade: OnboardingFacade) -> None:
    facade.initialise()
    facade.start()
    facade.set_draft("projects", ["Personal"])
    facade.complete()
    state = facade.restart()
    assert state.draft == {}
    assert state.completed_at is None
    assert state.skipped_at is None
    assert state.started_at is not None


def test_restart_from_a_missing_file_opens_the_wizard(tmp_path: Path) -> None:
    """An account that predates the wizard can still be given it."""
    facade = OnboardingFacade(tmp_path / "legacy" / STATE_FILENAME)
    assert facade.is_pending() is False
    state = facade.restart()
    assert state.status is OnboardingStatus.IN_PROGRESS
    assert facade.is_pending() is True


# ---------------------------------------------------------------------------
# Isolation and failure handling
# ---------------------------------------------------------------------------


def test_two_state_paths_do_not_share_state(tmp_path: Path) -> None:
    alice = OnboardingFacade(tmp_path / "alice" / STATE_FILENAME)
    bob = OnboardingFacade(tmp_path / "bob" / STATE_FILENAME)
    alice.initialise()
    alice.complete()
    assert alice.is_pending() is False
    assert bob.state().never_run is True
    assert bob.is_pending() is False


def test_failed_write_leaves_no_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state.json"

    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        write_state(path, OnboardingState())
    monkeypatch.undo()

    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".onboarding.")]
    assert leftovers == []
    assert not path.exists()
