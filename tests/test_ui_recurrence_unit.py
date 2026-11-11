"""Headless UI checks for recurrent tasks (ADR-033).

Drives the shared task create and edit dialogs with Streamlit's
``AppTest``: the real dialog code runs against the in-memory CalDAV
backend and the assertions read the rendered widget tree.

Covered:

- the create dialog exposes frequency, interval, anchor, and horizon;
- the horizon defaults to the per-user global default and can be
  overridden;
- the create dialog rejects a missing anchor;
- the edit dialog shows the scope choice only for recurrent tasks and
  dispatches to the matching scoped operation;
- the series inspection shows the effective horizon and the future
  occurrences;
- the horizon can be grown and shrunk from the edit dialog;
- cancelling the future asks for a confirmation first;
- the task table labels the recurrence, the horizon, and the role of
  every row.
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


class _FakeConfigs:
    """Config stand-in with a recurrence block."""

    def __init__(self, count: int = 1) -> None:
        self.recurrence = RecurrenceConfig(default_materialized_future_count=count)


class _FakeFacade:
    """Facade stand-in: a mock CalDAV plus a config block."""

    def __init__(self, caldav: MockCalDAVFacade, count: int = 1) -> None:
        self.caldav = caldav
        self.configs = _FakeConfigs(count)


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


def _run(monkeypatch: pytest.MonkeyPatch, task_uid: str | None = None) -> AppTest:
    """Render the dialog harness, optionally with the edit dialog."""
    if task_uid is None:
        monkeypatch.delenv("GENESIS_TEST_TASK_UID", raising=False)
    else:
        monkeypatch.setenv("GENESIS_TEST_TASK_UID", task_uid)
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
# Edit dialog
# ---------------------------------------------------------------------------


def test_edit_dialog_shows_the_scope_choice_for_recurrent_tasks(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _series(facade)
    at = _open_edit(monkeypatch, created.task.uid)

    scope = _widget_by_label(at.radio, "Applies to")
    assert scope.options == ["This instance", "All future instances"]


def test_edit_dialog_hides_the_scope_choice_for_plain_tasks(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = facade.caldav.create_task(TaskCreate(title="Plain"))
    at = _open_edit(monkeypatch, plain.uid)

    assert [radio.label for radio in at.radio] == []


def test_series_inspection_shows_the_horizon_and_the_futures(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _series(facade, count=4)
    at = _open_edit(monkeypatch, created.task.uid)

    assert "4 ahead" in _texts(at)
    assert "Future occurrences (4)" in _texts(at)
    horizon = _widget_by_label(at.number_input, "Future occurrences to keep ready")
    assert horizon.value == 4


def test_current_scope_save_uses_the_current_scope(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _series(facade, count=2)
    calls: list[tuple[str, str]] = []
    original = facade.caldav.update_task_scoped

    def spy(uid, data, *, scope="current", rule_change=None):
        calls.append((uid, scope))
        return original(uid, data, scope=scope, rule_change=rule_change)

    facade.caldav.update_task_scoped = spy  # type: ignore[method-assign]

    at = _open_edit(monkeypatch, created.task.uid)
    at.text_input(key="etd_" + created.task.uid + "_title").set_value("Only me").run()
    at.button(key="etd_" + created.task.uid + "_save").click().run()

    assert not at.exception, f"dialog raised: {at.exception}"
    assert calls == [(created.task.uid, "current")]


def test_all_future_save_uses_the_future_scope(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _series(facade, count=3)
    calls: list[tuple[str, str]] = []
    original = facade.caldav.update_task_scoped

    def spy(uid, data, *, scope="current", rule_change=None):
        calls.append((uid, scope))
        return original(uid, data, scope=scope, rule_change=rule_change)

    facade.caldav.update_task_scoped = spy  # type: ignore[method-assign]

    at = _open_edit(monkeypatch, created.task.uid)
    _widget_by_label(at.radio, "Applies to").set_value("All future instances").run()
    at.text_input(key="etd_" + created.task.uid + "_title").set_value("All of them").run()
    at.button(key="etd_" + created.task.uid + "_save").click().run()

    assert not at.exception, f"dialog raised: {at.exception}"
    assert calls == [(created.task.uid, "all-future")]
    tasks = facade.caldav.list_series_tasks(created.series.uid)
    titles = sorted(t.title for t in tasks)
    assert titles == ["All of them", "All of them", "All of them", "Pay rent"]


def test_increasing_the_horizon_creates_the_missing_rows(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _series(facade, count=1)
    at = _open_edit(monkeypatch, created.task.uid)
    _widget_by_label(at.number_input, "Future occurrences to keep ready").set_value(4).run()
    at.button(key="etd_" + created.task.uid + "_horizon_apply").click().run()

    assert not at.exception, f"dialog raised: {at.exception}"
    tasks = facade.caldav.list_series_tasks(created.series.uid)
    assert len(tasks) == 5


def test_decreasing_the_horizon_asks_for_confirmation(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _series(facade, count=4)
    at = _open_edit(monkeypatch, created.task.uid)
    _widget_by_label(at.number_input, "Future occurrences to keep ready").set_value(1).run()

    assert not at.exception, f"dialog raised: {at.exception}"
    confirm = _widget_by_label(at.checkbox, "Cancel the 3 farthest future occurrences")
    assert confirm is not None
    # The apply button stays disabled until the confirmation is ticked.
    apply_button = at.button(key="etd_" + created.task.uid + "_horizon_apply")
    assert apply_button.disabled

    confirm.set_value(True).run()
    at.button(key="etd_" + created.task.uid + "_horizon_apply").click().run()
    assert not at.exception, f"dialog raised: {at.exception}"

    tasks = facade.caldav.list_series_tasks(created.series.uid)
    cancelled = [t for t in tasks if t.status == TaskStatus.CANCELED]
    assert len(cancelled) == 3


def test_cancel_future_asks_for_confirmation(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _series(facade, count=3)
    at = _open_edit(monkeypatch, created.task.uid)

    cancel_button = at.button(key="etd_" + created.task.uid + "_cancel_future")
    assert cancel_button.disabled

    _widget_by_label(at.checkbox, "I understand that all future occurrences are cancelled").set_value(True).run()
    at.button(key="etd_" + created.task.uid + "_cancel_future").click().run()

    assert not at.exception, f"dialog raised: {at.exception}"
    tasks = facade.caldav.list_series_tasks(created.series.uid)
    cancelled = [t for t in tasks if t.status == TaskStatus.CANCELED]
    assert len(cancelled) == 3
    series = facade.caldav.get_series(created.series.uid)
    assert series is not None
    assert series.active is False


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

    futures = [
        t for t in facade.caldav.list_series_tasks(created.series.uid) if (t.recurrence_index or 0) > 0
    ]
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
# Inline edit scope
# ---------------------------------------------------------------------------


def test_inline_edit_scope_control_is_rendered_for_recurring_rows(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A table with recurring rows exposes the explicit scope control."""
    _series(facade, count=2)
    at = AppTest.from_file(TABLE_APP)
    at.run()
    assert not at.exception, f"table app raised: {at.exception}"
    scope = _widget_by_label(at.selectbox, "Inline edit scope")
    assert scope.value == "current"


def test_inline_edit_uses_the_current_scope_by_default(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _series(facade, count=2)
    task = created.task
    scope, content, status = _table.plan_inline_edit(
        "test_tasks",
        task,
        TaskUpdate(title="Only me"),
    )
    assert scope == "current"
    assert content.title == "Only me"
    assert status is None


def test_inline_edit_uses_the_future_scope_when_selected(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _series(facade, count=2)
    st.session_state["test_tasks__edit_scope"] = "all-future"
    scope, content, status = _table.plan_inline_edit(
        "test_tasks",
        created.task,
        TaskUpdate(title="All of them"),
    )
    assert scope == "all-future"
    assert content.title == "All of them"
    assert status is None


def test_inline_status_change_stays_on_the_occurrence(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _series(facade, count=2)
    st.session_state["test_tasks__edit_scope"] = "all-future"
    scope, content, status = _table.plan_inline_edit(
        "test_tasks",
        created.task,
        TaskUpdate(title="All of them", status=TaskStatus.COMPLETED),
    )
    assert scope == "all-future"
    assert content.title == "All of them"
    assert content.status is None
    assert status is not None
    assert status.status == TaskStatus.COMPLETED


def test_inline_scope_does_not_apply_to_plain_tasks(
    facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = facade.caldav.create_task(TaskCreate(title="Plain"))
    st.session_state["test_tasks__edit_scope"] = "all-future"
    scope, content, status = _table.plan_inline_edit(
        "test_tasks",
        plain,
        TaskUpdate(title="Renamed"),
    )
    assert scope == "current"
    assert content.title == "Renamed"
    assert status is None
