"""Onboarding wizard: the six-step first-run bootstrap.

A new account is walked through the system once: what Genesis
Productivity is for, the profile, projects, tasks, journal entries,
and contacts. Every entity list is the app's own table, and every
create button opens the app's own create dialog, so the wizard
teaches the real flows instead of parallel ones.

While the bootstrap is outstanding the wizard owns the session: the
app registers this page as the only page and hides the navigation
(see ``genesis.ui.app``). The step lives in the onboarding state
file, not in a local variable, so a refresh or the ``st.rerun()``
at the end of the reused task dialog does not lose the user's
place.

ADR-032.
"""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from genesis.onboarding import STEP_ORDER, OnboardingFacade, OnboardingState, OnboardingStatus, OnboardingStep
from genesis.profile import ProfileTarget
from genesis.ui._onboarding_copy import PROFILE_PROMPTS, STEP_COPY
from genesis.ui._render import (
    contact_create_dialog,
    journal_create_dialog,
    project_create_dialog,
    task_create_dialog,
)
from genesis.ui._state import get_facade
from genesis.ui._table import (
    render_contacts_table,
    render_journal_table,
    render_projects_table,
    render_tasks_table,
)

facade = get_facade()
onboarding: OnboardingFacade = facade.onboarding
state: OnboardingState = onboarding.state()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _current_step(state: OnboardingState) -> OnboardingStep:
    """Return the step the wizard resumes at."""
    if state.current_step:
        try:
            return OnboardingStep(state.current_step)
        except ValueError:
            pass
    return STEP_ORDER[0]


def _write_profile(target: ProfileTarget, text: str) -> bool:
    """Write the profile through the profile facade."""
    result = facade.profile.write(target, text, note="onboarding wizard")
    if not result.ok:
        st.error(result.error or "Could not save the file.")
    return result.ok


# -----------------------------------------------------------------------------
# Step bodies
# -----------------------------------------------------------------------------


def _render_purpose_step(state: OnboardingState) -> None:
    """The purpose step: copy only."""


def _render_profile_step(state: OnboardingState) -> Callable[[], None]:
    """The profile step: a guided write into ``facade.profile``."""
    target = ProfileTarget.PROFILE
    read = facade.profile.read(target)
    stored = read.content if read.ok else ""
    if "ob_profile_text" not in st.session_state:
        st.session_state["ob_profile_text"] = stored
    text = st.text_area(
        "Markdown",
        key="ob_profile_text",
        height=280,
        placeholder=PROFILE_PROMPTS,
    )
    if st.button("Save", key="ob_profile_save"):
        if _write_profile(target, text):
            st.success("Saved.")

    if not read.ok:
        st.warning("Your profile is still empty. Your agent will have no context about you.")

    def _on_leave() -> None:
        if text.strip() != stored.strip():
            _write_profile(target, text)

    return _on_leave


def _render_projects_step(state: OnboardingState) -> None:
    """The projects step: the projects table and the create dialog."""
    if st.button(
        "+ New Project",
        key="ob_add_project",
        type="primary",
        icon=":material/add:",
    ):
        project_create_dialog()
    render_projects_table(
        facade.caldav.list_projects(sort_by=["status", "name"]),
        caldav=facade.caldav,
        key="ob_projects",
    )


def _render_tasks_step(state: OnboardingState) -> None:
    """The tasks step: the tasks table and the create dialog."""
    if st.button(
        "+ Add Task",
        key="ob_add_task",
        type="primary",
        icon=":material/add:",
    ):
        task_create_dialog()
    render_tasks_table(
        facade.caldav.list_tasks(),
        caldav=facade.caldav,
        key="ob_tasks",
        show_actions=False,
    )


def _render_journals_step(state: OnboardingState) -> None:
    """The journals step: the journal table and the create dialog."""
    if st.button(
        "+ New Entry",
        key="ob_add_entry",
        type="primary",
        icon=":material/add:",
    ):
        journal_create_dialog()
    render_journal_table(
        facade.journal.list_entries(),
        key="ob_journal",
    )


def _render_contacts_step(state: OnboardingState) -> None:
    """The contacts step: the contacts table and the create dialog."""
    if st.button(
        "+ New Contact",
        key="ob_add_contact",
        type="primary",
        icon=":material/add:",
    ):
        contact_create_dialog()
    render_contacts_table(
        facade.carddav.list_contacts(),
        key="ob_contacts",
    )


_STEP_RENDERERS: dict[OnboardingStep, Callable[[OnboardingState], Callable[[], None] | None]] = {
    OnboardingStep.PURPOSE: _render_purpose_step,
    OnboardingStep.PROFILE: _render_profile_step,
    OnboardingStep.PROJECTS: _render_projects_step,
    OnboardingStep.TASKS: _render_tasks_step,
    OnboardingStep.JOURNALS: _render_journals_step,
    OnboardingStep.CONTACTS: _render_contacts_step,
}


# -----------------------------------------------------------------------------
# The wizard
# -----------------------------------------------------------------------------


def _render_wizard(state: OnboardingState) -> None:
    """Render the wizard: the current step and the footer."""
    if state.status is OnboardingStatus.NOT_STARTED:
        state = onboarding.start()

    step = _current_step(state)
    index = STEP_ORDER.index(step)
    copy = STEP_COPY[step]

    st.title("Setup")
    st.progress(
        (index + 1) / len(STEP_ORDER),
        text=f"Step {index + 1} of {len(STEP_ORDER)}",
    )
    st.subheader(copy["title"])
    st.markdown(copy["body"])
    st.divider()

    on_leave = _STEP_RENDERERS[step](state)

    st.divider()
    footer = st.columns([1, 1, 6])
    back_clicked = footer[0].button("Back", key="ob_back", disabled=index == 0)
    is_last = index == len(STEP_ORDER) - 1
    next_clicked = footer[1].button(
        "Finish" if is_last else "Next",
        key="ob_next",
        type="primary",
    )
    if footer[2].button("Skip setup", key="ob_skip"):
        onboarding.skip()
        st.rerun()

    if back_clicked:
        if on_leave is not None:
            on_leave()
        onboarding.set_step(STEP_ORDER[index - 1])
        st.rerun()

    if next_clicked:
        if on_leave is not None:
            on_leave()
        if is_last:
            onboarding.complete()
        else:
            onboarding.set_step(STEP_ORDER[index + 1])
        st.rerun()


# -----------------------------------------------------------------------------
# The terminal screen
# -----------------------------------------------------------------------------


def _recap() -> str:
    """Return a one-line recap of what the account now holds."""
    counts: list[str] = []
    try:
        counts.append(f"{len(facade.caldav.list_projects())} projects")
    except Exception:
        pass
    try:
        counts.append(f"{len(facade.caldav.list_tasks())} tasks")
    except Exception:
        pass
    try:
        counts.append(f"{len(facade.journal.list_entries())} journal entries")
    except Exception:
        pass
    try:
        counts.append(f"{len(facade.carddav.list_contacts())} contacts")
    except Exception:
        pass
    if not counts:
        return "Your account is ready."
    return "Your account now holds " + ", ".join(counts) + "."


def _render_terminal(state: OnboardingState) -> None:
    """Render the screen shown once the bootstrap is finished."""
    st.title("Setup")
    if state.status is OnboardingStatus.COMPLETED:
        st.success("Setup complete.")
        if state.completed_at is not None:
            st.caption(f"Completed on {state.completed_at:%Y-%m-%d %H:%M} UTC.")
    else:
        st.info("Setup skipped.")
        if state.skipped_at is not None:
            st.caption(f"Skipped on {state.skipped_at:%Y-%m-%d %H:%M} UTC.")
    st.write(_recap())
    st.page_link(
        "pages/dashboard.py",
        label="Go to the Dashboard",
        icon=":material/dashboard:",
    )
    st.caption("You can run the setup wizard again from the Settings page.")


# -----------------------------------------------------------------------------
# Dispatch
# -----------------------------------------------------------------------------


if state.is_terminal:
    _render_terminal(state)
else:
    _render_wizard(state)
