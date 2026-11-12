"""Headless UI checks for recurrent tasks (ADR-033).

Drives the shared task create and edit dialogs, the series form, the
series table, and the delete confirmation with Streamlit's
``AppTest``: the real dialog code runs against the in-memory CalDAV
backend and the assertions read the rendered widget tree.

Covered:

- the create dialog exposes frequency, interval, anchor, and horizon;
- the horizon defaults to the per-user global default and can be
  overridden;
- the create dialog rejects a missing anchor;
- the inline scope control is gone, and the edit dialog of an
  occurrence has no scope choice;
- the series-owned fields of an occurrence are disabled, and the
  caption names the series that owns them;
- the occurrence edit changes that occurrence only;
- the series table renders one row per series with its rule, its
  dates, its horizon, its next occurrence, and its status;
- the series form shows the fields and the effect statement, and the
  save applies the change;
- the series cancellation asks for a confirmation and then cancels;
- an inline edit of a series-owned field is refused;
- the delete dialog states the consequence and offers the series
  cancellation.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import cast

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from genesis.caldav.facade import CalDAVFacade
from genesis.configs import RecurrenceConfig
from genesis.entities import (
    RecurrenceAnchor,
    RecurrenceFrequency,
    RecurrenceRule,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
)
from genesis.ui import _render, _state, _table

from .mock_caldav import MockCalDAVFacade

APP = Path(__file__).parent / "_task_recurrence_app.py"
TABLE_APP = Path(__file__).parent / "_task_table_app.py"
TASKS_APP = Path(__file__).parent / "_tasks_page_app.py"


class _FakeConfigs:
    """Config stand-in with a recurrence block."""

    def __init__(self, count: int = 1) -> None:
        self.recurrence = RecurrenceConfig(default_materialized_future_count=count)


class _FakeJournal:
    """Journal stand-in: the Tasks page asks for the linked entries."""

    def list_entries(self, task_id: str | None = None) -> list:
        return []


class _FakeFacade:
    """Facade stand-in: a mock CalDAV plus a config block."""

    def __init__(self, caldav: MockCalDAVFacade, count: int = 1) -> None:
        self.caldav = caldav
        self.configs = _FakeConfigs(count)
        self.journal = _FakeJournal()

    def create_recurrent_task(
        self,
        data,
        *,
        frequency,
        interval=1,
        anchor,
        materialized_future_count=None,
    ):
        """Create a recurrent task, resolving the global default.

        Mirrors ``genesis.core.facade.Facade.create_recurrent_task``.
        """
        count = materialized_future_count
        if count is None:
            count = self.configs.recurrence.default_materialized_future_count
        rule = RecurrenceRule(
            frequency=RecurrenceFrequency(frequency),
            interval=interval,
            anchor=RecurrenceAnchor(anchor),
        )
        return self.caldav.create_recurrent_task(
            data,
            rule=rule,
            materialized_future_count=count,
        )

    def update_series(self, series_uid, changes):
        """Apply a series edit (ADR-033)."""
        return self.caldav.update_series(series_uid, changes)

    def cancel_series(self, series_uid):
        """Cancel a series (ADR-033)."""
        return self.caldav.cancel_series(series_uid)

    def list_series_summaries(self):
        """Return the summary row of every recurrence series."""
        return self.caldav.list_series_summaries()


@pytest.fixture
def facade(monkeypatch: pytest.MonkeyPatch) -> _FakeFacade:
    """Patch every ``get_facade`` binding the dialogs use."""
    caldav = MockCalDAVFacade()
    fake = _FakeFacade(caldav)
    for module in (_state, _render, _table):
        monkeypatch.setattr(module, "get_facade", lambda: fake)
    return fake


def _series(facade: _FakeFacade, count: int = 4, title: str = "Pay rent"):
    """Create a recurrent task and return the creation result."""
    return facade.caldav.create_recurrent_task(
        TaskCreate(title=title, assigned_date=date(2026, 1, 31)),
        rule=RecurrenceRule(
            frequency=RecurrenceFrequency.MONTHLY,
            interval=1,
            anchor=RecurrenceAnchor.ASSIGNED_DATE,
        ),
        materialized_future_count=count,
    )


def _run(
    monkeypatch: pytest.MonkeyPatch,
    task_uid: str | None = None,
    series_uid: str | None = None,
) -> AppTest:
    """Render the dialog harness, optionally with the edit dialog."""
    if task_uid is None:
        monkeypatch.delenv("GENESIS_TEST_TASK_UID", raising=False)
    else:
        monkeypatch.setenv("GENESIS_TEST_TASK_UID", task_uid)
    if series_uid is None:
        monkeypatch.delenv("GENESIS_TEST_SERIES_UID", raising=False)
    else:
        monkeypatch.setenv("GENESIS_TEST_SERIES_UID", series_uid)
    at = AppTest.from_file(APP)
    at.run()
    assert not at.exception, f"app raised: {at.exception}"
    return at


def _open_create(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    """Open the create-task dialog and return the running app."""
    at = _run(monkeypatch)
    at.button(key="open_create_dialog").click().run()
    assert not at.exception, f"dialog raised: {at.exception}"
    return at


def _open_edit(monkeypatch: pytest.MonkeyPatch, uid: str) -> AppTest:
    """Open the edit-task dialog for the given task and return the app."""
    at = _run(monkeypatch, uid)
    at.button(key="open_edit_dialog").click().run()
    assert not at.exception, f"dialog raised: {at.exception}"
    return at


def _open_series(monkeypatch: pytest.MonkeyPatch, series_uid: str) -> AppTest:
    """Open the series form and return the running app."""
    at = _run(monkeypatch, series_uid=series_uid)
    at.button(key="open_series_dialog").click().run()
    assert not at.exception, f"dialog raised: {at.exception}"
    return at


def _open_series_cancel(monkeypatch: pytest.MonkeyPatch, series_uid: str) -> AppTest:
    """Open the series cancellation confirmation and return the app."""
    at = _run(monkeypatch, series_uid=series_uid)
    at.button(key="open_series_cancel_dialog").click().run()
    assert not at.exception, f"dialog raised: {at.exception}"
    return at


def _open_delete(monkeypatch: pytest.MonkeyPatch, task_uid: str) -> AppTest:
    """Open the delete confirmation for the given task."""
    at = _run(monkeypatch, task_uid)
    at.button(key="open_delete_dialog").click().run()
    assert not at.exception, f"dialog raised: {at.exception}"
    return at


def _widget_by_label(widgets, label: str):
    """Return the first widget carrying the given label."""
    for widget in widgets:
        if widget.label == label:
            return widget
    raise AssertionError(f"no widget labelled {label!r}")


def _texts(at: AppTest) -> str:
    """All rendered text of the page, as one blob."""
    parts = [element.value for element in at.markdown]
    parts += [element.value for element in at.caption]
    parts += [element.value for element in at.warning]
    parts += [element.value for element in at.error]
    parts += [element.value for element in at.info]
    parts += [element.label for element in at.expander]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Create dialog
# ---------------------------------------------------------------------------


def test_create_dialog_exposes_the_recurrence_controls(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _open_create(monkeypatch)

    assert _widget_by_label(at.checkbox, "Recurring task") is not None
    at.checkbox(key="_ntd_recurring").set_value(True).run()
    assert not at.exception, f"dialog raised: {at.exception}"

    labels = {box.label for box in at.selectbox}
    assert "Repeats" in labels
    assert "Anchor" in labels
    assert _widget_by_label(at.number_input, "Every (units)") is not None
    assert _widget_by_label(at.number_input, "Future occurrences to keep ready") is not None


def test_create_horizon_defaults_to_the_global_default(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The horizon input is seeded from the per-user default."""
    facade.configs.recurrence = RecurrenceConfig(default_materialized_future_count=4)
    at = _open_create(monkeypatch)
    at.checkbox(key="_ntd_recurring").set_value(True).run()

    horizon = _widget_by_label(at.number_input, "Future occurrences to keep ready")
    assert horizon.value == 4


def test_user_can_override_the_create_horizon(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _open_create(monkeypatch)
    at.checkbox(key="_ntd_recurring").set_value(True).run()
    _widget_by_label(at.number_input, "Future occurrences to keep ready").set_value(3).run()
    at.text_input(key="_ntd_title").set_value("Pay rent")
    at.date_input(key="_ntd_assigned").set_value(date(2026, 1, 31)).run()
    at.button(key="_ntd_add").click().run()

    assert not at.exception, f"dialog raised: {at.exception}"
    series = facade.caldav.list_series()
    assert len(series) == 1
    assert series[0].materialized_future_count == 3
    assert len(facade.caldav.list_series_tasks(series[0].uid)) == 4


def test_create_dialog_rejects_a_missing_anchor(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _open_create(monkeypatch)
    at.checkbox(key="_ntd_recurring").set_value(True).run()
    at.text_input(key="_ntd_title").set_value("Pay rent").run()
    at.button(key="_ntd_add").click().run()

    assert not at.exception, f"dialog raised: {at.exception}"
    assert at.error
    assert "assigned_date" in at.error[0].value
    assert facade.caldav.list_series() == []


# ---------------------------------------------------------------------------
# Occurrence edit dialog
# ---------------------------------------------------------------------------


def test_the_edit_dialog_has_no_scope_choice(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The edit scope is gone: an occurrence edit is instance-only."""
    created = _series(facade)
    at = _open_edit(monkeypatch, created.task.uid)

    assert [radio.label for radio in at.radio] == []


def test_the_edit_dialog_disables_the_series_owned_fields(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The schedule and the project belong to the series."""
    created = _series(facade)
    at = _open_edit(monkeypatch, created.task.uid)

    assert _widget_by_label(at.date_input, "assigned_date").disabled
    assert _widget_by_label(at.date_input, "hard_deadline").disabled
    assert _widget_by_label(at.date_input, "scheduled_start_date").disabled
    assert _widget_by_label(at.number_input, "Duration (min)").disabled
    assert _widget_by_label(at.selectbox, "Project").disabled


def test_the_edit_dialog_keeps_the_content_editable(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Title, description, priority, and status stay editable."""
    created = _series(facade)
    at = _open_edit(monkeypatch, created.task.uid)

    assert not _widget_by_label(at.text_input, "Title").disabled
    assert not _widget_by_label(at.text_area, "Description").disabled
    assert not _widget_by_label(at.number_input, "Priority").disabled
    assert not _widget_by_label(at.selectbox, "Status").disabled


def test_the_edit_dialog_names_the_owning_series(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caption points at the series that owns the schedule."""
    created = _series(facade)
    at = _open_edit(monkeypatch, created.task.uid)

    texts = _texts(at)
    assert created.series.uid in texts
    assert "Edit the series to change them" in texts
    assert "Recurrence series table" in texts


def test_the_edit_dialog_has_no_series_cancel_button(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cancellation lives in the series table, not in the dialog."""
    created = _series(facade)
    at = _open_edit(monkeypatch, created.task.uid)

    labels = [button.label for button in at.button]
    assert "Cancel future" not in labels
    assert "Cancel series" not in labels


def test_the_occurrence_edit_changes_that_occurrence_only(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _series(facade, count=3)
    at = _open_edit(monkeypatch, created.task.uid)
    at.text_input(key="etd_" + created.task.uid + "_title").set_value("Pay rent (January)").run()
    at.button(key="etd_" + created.task.uid + "_save").click().run()

    assert not at.exception, f"dialog raised: {at.exception}"
    tasks = facade.caldav.list_series_tasks(created.series.uid)
    titles = sorted(t.title for t in tasks)
    assert titles == ["Pay rent", "Pay rent", "Pay rent", "Pay rent (January)"]
    series = facade.caldav.get_series(created.series.uid)
    assert series is not None
    assert series.template.title == "Pay rent"


def test_the_occurrence_edit_keeps_the_schedule_untouched(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dates of an occurrence are not part of its edit."""
    created = _series(facade, count=2)
    at = _open_edit(monkeypatch, created.task.uid)
    at.text_input(key="etd_" + created.task.uid + "_title").set_value("Pay rent (January)").run()
    at.button(key="etd_" + created.task.uid + "_save").click().run()

    assert not at.exception, f"dialog raised: {at.exception}"
    stored = facade.caldav.get_task(created.task.uid)
    assert stored is not None
    assert stored.assigned_date == date(2026, 1, 31)


# ---------------------------------------------------------------------------
# Series table
# ---------------------------------------------------------------------------


def test_the_series_table_renders_one_row_per_series(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The series table lists the rule, the dates, and the status."""
    created = _series(facade, count=4)
    at = AppTest.from_file(TASKS_APP)
    at.run()
    assert not at.exception, f"tasks page raised: {at.exception}"

    table = next(frame for frame in at.dataframe if frame.key == "global_series")
    row = table.value.iloc[0]
    assert row["Title"] == "Pay rent"
    assert row["Repeats"] == "Monthly, every 1"
    assert row["Anchor"] == "assigned_date"
    assert row["Start"] == "2026-01-31"
    assert row["End"] == "no end"
    assert row["Ready"] == 4
    assert row["Next"] == "2026-02-28"
    assert row["Current"] == created.task.uid
    assert row["Status"] == "Active"


def test_the_series_table_is_empty_without_a_series(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = AppTest.from_file(TASKS_APP)
    at.run()
    assert not at.exception, f"tasks page raised: {at.exception}"
    assert "No recurrence series." in _texts(at)


# ---------------------------------------------------------------------------
# Series form
# ---------------------------------------------------------------------------


def test_the_series_form_exposes_the_fields(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _series(facade, count=4)
    at = _open_series(monkeypatch, created.series.uid)

    labels = {box.label for box in at.selectbox}
    assert "Repeats" in labels
    assert "Anchor" in labels
    assert "Project" in labels
    assert _widget_by_label(at.number_input, "Every (units)") is not None
    assert _widget_by_label(at.number_input, "Future occurrences to keep ready") is not None
    assert _widget_by_label(at.date_input, "Start date") is not None
    assert _widget_by_label(at.date_input, "End date") is not None
    assert _widget_by_label(at.checkbox, "No end date") is not None


def test_the_series_form_states_the_effect(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The form says what the change does before it is saved."""
    created = _series(facade, count=4)
    at = _open_series(monkeypatch, created.series.uid)

    texts = _texts(at)
    assert "open occurrences of this series" in texts
    assert "Completed history is not changed." in texts


def test_the_series_form_saves_the_series(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The save applies the change to the series."""
    created = _series(facade, count=4)
    at = _open_series(monkeypatch, created.series.uid)
    _widget_by_label(at.number_input, "Every (units)").set_value(2).run()
    _widget_by_label(at.button, "Save").click().run()

    assert not at.exception, f"dialog raised: {at.exception}"
    series = facade.caldav.get_series(created.series.uid)
    assert series is not None
    assert series.rule.interval == 2
    tasks = facade.caldav.list_series_tasks(created.series.uid)
    assert tasks[1].assigned_date == date(2026, 3, 31)


def test_the_series_form_saves_the_content(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The content template is applied to the open occurrences."""
    created = _series(facade, count=2)
    at = _open_series(monkeypatch, created.series.uid)
    at.text_input(key="sed_" + created.series.uid + "_title").set_value("Pay the rent").run()
    _widget_by_label(at.button, "Save").click().run()

    assert not at.exception, f"dialog raised: {at.exception}"
    tasks = facade.caldav.list_series_tasks(created.series.uid)
    assert {t.title for t in tasks} == {"Pay the rent"}


# ---------------------------------------------------------------------------
# Series cancellation
# ---------------------------------------------------------------------------


def test_the_series_cancellation_asks_for_a_confirmation(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _series(facade, count=3)
    at = _open_series_cancel(monkeypatch, created.series.uid)

    assert "Cancel the series" in _texts(at)
    # Nothing is cancelled before the confirmation.
    series = facade.caldav.get_series(created.series.uid)
    assert series is not None
    assert series.active is True


def test_the_series_cancellation_cancels_the_series(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _series(facade, count=3)
    at = _open_series_cancel(monkeypatch, created.series.uid)
    at.button(key="scd_confirm_" + created.series.uid).click().run()

    assert not at.exception, f"dialog raised: {at.exception}"
    series = facade.caldav.get_series(created.series.uid)
    assert series is not None
    assert series.active is False
    assert series.stop_reason == "cancelled"
    tasks = facade.caldav.list_series_tasks(created.series.uid)
    assert tasks[0].status == TaskStatus.NEEDS_ACTION
    assert all(t.status == TaskStatus.CANCELED for t in tasks[1:])


# ---------------------------------------------------------------------------
# Task table
# ---------------------------------------------------------------------------


def test_table_labels_the_recurrence_and_the_occurrence_role(
    facade: _FakeFacade,
) -> None:
    created = _series(facade, count=4)
    labels = _table.recurrence_labels(
        facade.caldav.list_tasks(),
        cast(CalDAVFacade, facade.caldav),
    )

    current = labels[created.task.uid]
    assert current[0] == "Monthly, 4 ahead"
    assert current[1] == "Current"
    assert current[2] == created.series.uid

    futures = [t for t in facade.caldav.list_series_tasks(created.series.uid) if (t.recurrence_index or 0) > 0]
    roles = [labels[t.uid][1] for t in sorted(futures, key=lambda t: t.recurrence_index or 0)]
    assert roles == ["Future 1 of 4", "Future 2 of 4", "Future 3 of 4", "Future 4 of 4"]


def test_table_labels_completed_occurrences_as_history(
    facade: _FakeFacade,
) -> None:
    created = _series(facade, count=2)
    facade.caldav.update_task(created.task.uid, TaskUpdate(status=TaskStatus.COMPLETED))
    labels = _table.recurrence_labels(
        facade.caldav.list_tasks(),
        cast(CalDAVFacade, facade.caldav),
    )
    assert labels[created.task.uid][1] == "History"


def test_table_leaves_plain_tasks_unlabelled(facade: _FakeFacade) -> None:
    plain = facade.caldav.create_task(TaskCreate(title="Plain"))
    labels = _table.recurrence_labels(
        facade.caldav.list_tasks(),
        cast(CalDAVFacade, facade.caldav),
    )
    assert labels[plain.uid] == ("", "", "")


# ---------------------------------------------------------------------------
# Inline edit
# ---------------------------------------------------------------------------


def test_the_inline_scope_control_is_not_rendered(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inline scope dropdown is gone."""
    _series(facade, count=2)
    at = AppTest.from_file(TABLE_APP)
    at.run()
    assert not at.exception, f"table app raised: {at.exception}"
    assert [box.label for box in at.selectbox] == []


def test_an_inline_edit_of_a_series_owned_field_is_refused(
    facade: _FakeFacade,
) -> None:
    """The schedule of an occurrence belongs to its series."""
    created = _series(facade, count=2)
    error = _table.apply_inline_task_edit(
        cast(CalDAVFacade, facade.caldav),
        created.task,
        TaskUpdate(assigned_date=date(2026, 3, 5)),
    )

    assert error is not None
    assert "assigned_date" in error
    assert created.series.uid in error
    # Nothing was written.
    stored = facade.caldav.get_task(created.task.uid)
    assert stored is not None
    assert stored.assigned_date == date(2026, 1, 31)


def test_an_inline_edit_of_the_content_is_applied(
    facade: _FakeFacade,
) -> None:
    """A content edit of an occurrence is applied to that occurrence."""
    created = _series(facade, count=3)
    error = _table.apply_inline_task_edit(
        cast(CalDAVFacade, facade.caldav),
        created.task,
        TaskUpdate(title="Pay rent (January)"),
    )

    assert error is None
    tasks = facade.caldav.list_series_tasks(created.series.uid)
    titles = sorted(t.title for t in tasks)
    assert titles == ["Pay rent", "Pay rent", "Pay rent", "Pay rent (January)"]


# ---------------------------------------------------------------------------
# Delete confirmation
# ---------------------------------------------------------------------------


def test_the_delete_dialog_states_the_consequence(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The delete of an occurrence says what happens to the series."""
    created = _series(facade, count=2)
    at = _open_delete(monkeypatch, created.task.uid)

    texts = _texts(at)
    assert "removed for good" in texts
    assert "becomes the current one" in texts
    assert _widget_by_label(at.checkbox, "Also cancel the recurrence series") is not None


def test_the_delete_of_a_future_occurrence_states_the_consequence(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future occurrence is removed; the series keeps running."""
    created = _series(facade, count=2)
    future = next(t for t in facade.caldav.list_series_tasks(created.series.uid) if (t.recurrence_index or 0) == 1)
    at = _open_delete(monkeypatch, future.uid)

    texts = _texts(at)
    assert "The series keeps running" in texts
    assert "becomes the current one" not in texts


def test_the_delete_dialog_can_cancel_the_series(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The series cancellation is offered in the delete dialog."""
    created = _series(facade, count=2)
    at = _open_delete(monkeypatch, created.task.uid)
    _widget_by_label(at.checkbox, "Also cancel the recurrence series").set_value(True).run()
    _widget_by_label(at.button, "Delete").click().run()

    assert not at.exception, f"dialog raised: {at.exception}"
    series = facade.caldav.get_series(created.series.uid)
    assert series is not None
    assert series.active is False
    assert series.stop_reason == "cancelled"


def test_the_delete_of_an_ordinary_task_has_no_series_option(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = facade.caldav.create_task(TaskCreate(title="Plain"))
    at = _open_delete(monkeypatch, plain.uid)

    assert [box.label for box in at.checkbox] == []


# ---------------------------------------------------------------------------
# Create dialog state
# ---------------------------------------------------------------------------


def test_create_dialog_state_is_cleared_on_close() -> None:
    """The create dialog drops its widget state when it closes.

    Otherwise a value typed during an earlier open would be reused,
    including the materialized-future count that the global default
    seeds.
    """
    st.session_state["_ntd_title"] = "Pay rent"
    st.session_state["_ntd_horizon_1"] = 7
    st.session_state["unrelated"] = "keep"

    _render._clear_create_dialog_state()

    assert "_ntd_title" not in st.session_state
    assert "_ntd_horizon_1" not in st.session_state
    assert st.session_state["unrelated"] == "keep"


def test_create_horizon_follows_the_global_default(facade: _FakeFacade) -> None:
    """A new widget starts from the current global default.

    The widget key carries the resolved default, so a changed default
    cannot be shadowed by a value typed during an earlier open.
    """
    facade.configs.recurrence = RecurrenceConfig(default_materialized_future_count=1)
    first = _render.default_materialized_future_count(facade)
    facade.configs.recurrence = RecurrenceConfig(default_materialized_future_count=4)
    second = _render.default_materialized_future_count(facade)
    assert (first, second) == (1, 4)
