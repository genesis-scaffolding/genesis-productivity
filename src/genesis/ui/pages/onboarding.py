"""Onboarding wizard: the seven-step first-run bootstrap.

A new account is walked through the system once: what Genesis is
for, the profile, the life story, starter projects, one real task,
the journal's period anchoring, and the contact book. Every step
writes through the facade that owns the data, so nothing here is a
parallel write path.

While the bootstrap is outstanding the wizard owns the session: the
app registers this page as the only page and hides the navigation
(see ``genesis.ui.app``). The step lives in the onboarding state
file, not in a local variable, so a refresh or the ``st.rerun()``
at the end of the reused task dialog does not lose the user's
place.

ADR-032.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import date

import streamlit as st

from genesis.entities import ContactCreate, ProjectCreate
from genesis.journal.facade import Entry, EntryType
from genesis.onboarding import STEP_ORDER, OnboardingFacade, OnboardingState, OnboardingStatus, OnboardingStep
from genesis.profile import ProfileTarget
from genesis.ui._onboarding_copy import (
    LIFE_STORY_PROMPTS,
    PROFILE_PROMPTS,
    STARTER_PROJECTS,
    STEP_COPY,
)
from genesis.ui._render import task_create_dialog
from genesis.ui._state import get_facade
from genesis.ui._table import render_tasks_table

facade = get_facade()
onboarding: OnboardingFacade = facade.onboarding
state: OnboardingState = onboarding.state()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _slug(name: str) -> str:
    """Return a widget-key-safe slug for a project name.

    A short digest is appended so two names that slug to the same
    string ("My Project" and "My-Project") still get their own
    widget key.
    """
    slug = "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower())
    digest = hashlib.sha1(name.strip().encode("utf-8")).hexdigest()[:6]
    return f"{slug}_{digest}"


def _draft_list(state: OnboardingState, key: str, fallback: list[str]) -> list[str]:
    """Return a draft entry as a list of strings."""
    raw = state.draft.get(key)
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str) and raw:
        return [raw]
    return list(fallback)


def _current_step(state: OnboardingState) -> OnboardingStep:
    """Return the step the wizard resumes at."""
    if state.current_step:
        try:
            return OnboardingStep(state.current_step)
        except ValueError:
            pass
    return STEP_ORDER[0]


def _write_profile(target: ProfileTarget, text: str) -> bool:
    """Write the profile or the life story through the profile facade."""
    result = facade.profile.write(target, text, note="onboarding wizard")
    if not result.ok:
        st.error(result.error or "Could not save the file.")
    return result.ok


def _create_projects(names: list[str]) -> list[str]:
    """Create the projects that do not exist yet.

    The name match is case-insensitive, so running the wizard twice
    does not produce duplicates.
    """
    existing = {p.name.strip().lower() for p in facade.caldav.list_projects()}
    created: list[str] = []
    for name in names:
        clean = name.strip()
        if not clean or clean.lower() in existing:
            continue
        facade.caldav.create_project(ProjectCreate(name=clean))
        existing.add(clean.lower())
        created.append(clean)
    return created


# -----------------------------------------------------------------------------
# Step bodies
# -----------------------------------------------------------------------------


def _render_purpose_step(state: OnboardingState) -> None:
    """The purpose step: copy only."""


def _render_profile_step(state: OnboardingState) -> Callable[[], None]:
    """The profile step: a guided write into ``facade.profile``."""
    return _render_text_step(
        state,
        target=ProfileTarget.PROFILE,
        text_key="ob_profile_text",
        save_key="ob_profile_save",
        placeholder=PROFILE_PROMPTS,
        empty_warning="Your profile is still empty. Your assistant will have no context about you.",
    )


def _render_life_story_step(state: OnboardingState) -> Callable[[], None]:
    """The life story step: a guided write into ``facade.profile``."""
    return _render_text_step(
        state,
        target=ProfileTarget.LIFE_STORY,
        text_key="ob_life_text",
        save_key="ob_life_save",
        placeholder=LIFE_STORY_PROMPTS,
        empty_warning="Your life story is still empty. That is fine, it is optional.",
    )


def _render_text_step(
    state: OnboardingState,
    *,
    target: ProfileTarget,
    text_key: str,
    save_key: str,
    placeholder: str,
    empty_warning: str,
) -> Callable[[], None]:
    """Render a text step that writes through the profile facade.

    Returns the hook that runs when the user leaves the step: it
    saves what they typed if it differs from what is stored, so
    moving on never loses the text.
    """
    read = facade.profile.read(target)
    stored = read.content if read.ok else ""
    if text_key not in st.session_state:
        st.session_state[text_key] = stored
    text = st.text_area(
        "Markdown",
        key=text_key,
        height=280,
        placeholder=placeholder,
    )
    if st.button("Save", key=save_key):
        if _write_profile(target, text):
            st.success("Saved.")

    if not read.ok:
        st.warning(empty_warning)

    def _on_leave() -> None:
        if text.strip() != stored.strip():
            _write_profile(target, text)

    return _on_leave


def _render_projects_step(state: OnboardingState) -> Callable[[], None]:
    """The projects step: starter projects, preselected."""
    known = _draft_list(state, "project_names", list(STARTER_PROJECTS))
    selected = _draft_list(state, "projects", list(STARTER_PROJECTS))

    with st.form("ob_proj_form", clear_on_submit=True):
        new_name = st.text_input(
            "New project name",
            key="ob_proj_new",
            placeholder="Another project you want to start",
        )
        add_clicked = st.form_submit_button("Add project", key="ob_proj_add")
    if add_clicked and new_name.strip():
        name = new_name.strip()
        if name not in known:
            known.append(name)
        if name not in selected:
            selected.append(name)
        onboarding.set_draft("project_names", known)
        onboarding.set_draft("projects", selected)

    for name in known:
        key = f"ob_proj_{_slug(name)}"
        if key not in st.session_state:
            st.session_state[key] = name in selected
        st.checkbox(name, key=key)

    current = [name for name in known if bool(st.session_state.get(f"ob_proj_{_slug(name)}"))]
    if current != selected:
        onboarding.set_draft("projects", current)

    def _on_leave() -> None:
        names = [name for name in known if bool(st.session_state.get(f"ob_proj_{_slug(name)}"))]
        _create_projects(names)

    return _on_leave


def _render_tasks_step(state: OnboardingState) -> None:
    """The tasks step: the existing add form and the existing table."""
    if st.button("+ Add Task", key="ob_add_task"):
        task_create_dialog()
    tasks = facade.caldav.list_tasks()
    render_tasks_table(tasks, caldav=facade.caldav, key="ob_tasks")


def _render_journals_step(state: OnboardingState) -> None:
    """The journals step: the period grid and today's daily entry."""
    today = date.today()
    journal = facade.journal

    grid = st.columns(4)
    for column, entry_type in zip(
        grid,
        (EntryType.DAILY, EntryType.WEEKLY, EntryType.MONTHLY, EntryType.YEARLY),
        strict=True,
    ):
        with column:
            with st.container(border=True):
                st.caption(entry_type.value.capitalize())
                if journal.find_for_period(entry_type, today) is None:
                    st.write("Empty")
                else:
                    st.write("Written")

    existing = journal.find_for_period(EntryType.DAILY, today)
    if "ob_journal_text" not in st.session_state:
        st.session_state["ob_journal_text"] = existing.content if existing is not None else ""
    text = st.text_area(
        "Today's entry",
        key="ob_journal_text",
        height=200,
        placeholder="What is on your mind today?",
    )
    if st.button("Save today's daily entry", key="ob_journal_save", disabled=existing is not None):
        try:
            journal.save_entry(
                Entry(
                    uid=uuid.uuid4().hex,
                    date=today,
                    type=EntryType.DAILY,
                    content=text,
                )
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.success("Saved.")
    if existing is not None:
        st.info("You already have a daily entry for today. Open the Journal page to read or edit it.")


def _render_contacts_step(state: OnboardingState) -> None:
    """The contacts step: one contact, created through the facade."""
    with st.form("ob_contact_form", clear_on_submit=True):
        name = st.text_input("Name", key="ob_contact_name")
        email = st.text_input("Email (optional)", key="ob_contact_email")
        add_clicked = st.form_submit_button("Add contact", key="ob_contact_add")
    if add_clicked:
        clean = name.strip()
        if not clean:
            st.error("Name is required.")
        else:
            contact = facade.carddav.create_contact(
                ContactCreate(
                    name=clean,
                    emails=[email.strip()] if email.strip() else [],
                )
            )
            st.success(f"Added {contact.name}.")

    count = len(facade.carddav.list_contacts())
    st.caption(f"Your contact book holds {count} contact{'s' if count != 1 else ''}.")


_STEP_RENDERERS: dict[OnboardingStep, Callable[[OnboardingState], Callable[[], None] | None]] = {
    OnboardingStep.PURPOSE: _render_purpose_step,
    OnboardingStep.PROFILE: _render_profile_step,
    OnboardingStep.LIFE_STORY: _render_life_story_step,
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
