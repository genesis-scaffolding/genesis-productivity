"""Headless regression tests for the row-bound table widget (ADR-034).

The shared task table dispatches an inline edit by row position. The
data editor keeps its editing state in the browser, keyed by row
position, and sends the whole accumulated state on every interaction.
The two only agree while the rendered rows do not change. A series
breaks that: completing an occurrence makes the series materialise a
replacement, so the table still shows the same number of rows, with
another task in the slot the user edited.

The widget is therefore bound to the row identity map: its key
carries the signature of the rendered row UIDs, so a change of the
rows replaces the widget and drops the pending edit.

The tests drive the real pages with Streamlit's ``AppTest`` and model
the browser's editing state, so the payload the table receives is the
one the browser would send.

Covered:

- the widget identity follows the row identity, and is stable when
  the rows do not change;
- a payload of a superseded row map is never dispatched;
- the next inline edit applies to its own row only;
- an ordinary task completion still works;
- a re-sort and a filter change drop the pending edit;
- the projects table and the journal table bind their rows as well.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from streamlit.proto.WidgetStates_pb2 import WidgetStates
from streamlit.testing.v1 import AppTest

from genesis.entities import (
    ProjectCreate,
    RecurrenceAnchor,
    RecurrenceFrequency,
    RecurrenceRule,
    TaskCreate,
    TaskStatus,
    TaskUpdate,
)
from genesis.journal.facade import Entry
from genesis.ui import _render, _state, _table

from .mock_caldav import MockCalDAVFacade

TASKS_APP = Path(__file__).parent / "_tasks_page_app.py"

# The projects and the journal tables have no page harness of their
# own, so the test renders them from a script.
PROJECTS_APP = """
from genesis.ui._state import get_facade
from genesis.ui._table import render_projects_table

facade = get_facade()
render_projects_table(
    facade.caldav.list_projects(),
    caldav=facade.caldav,
    key="identity_projects",
)
"""

JOURNAL_APP = """
from genesis.ui._state import get_facade
from genesis.ui._table import render_journal_table

facade = get_facade()
render_journal_table(facade.journal.list_entries(), key="identity_journal")
"""


class _FakeJournal:
    """Journal stand-in: a list of entries in memory."""

    def __init__(self) -> None:
        self.entries: list[Entry] = []

    def list_entries(self, task_id: str | None = None) -> list[Entry]:
        if task_id is None:
            return list(self.entries)
        return [entry for entry in self.entries if entry.task_id == task_id]

    def save_entry(self, entry: Entry) -> Entry:
        for index, existing in enumerate(self.entries):
            if existing.uid == entry.uid:
                self.entries[index] = entry
                return entry
        self.entries.append(entry)
        return entry


class _FakeFacade:
    """Facade stand-in: a mock CalDAV, an in-memory journal, and the
    XP hooks the table uses."""

    def __init__(self, caldav: MockCalDAVFacade) -> None:
        self.caldav = caldav
        self.journal = _FakeJournal()

    def list_series_summaries(self):
        """Return the summary row of every recurrence series."""
        from genesis.caldav import recurrence

        return recurrence.series_summaries(self.caldav._recurrence_store())

    def record_xp_event(self, *, task_id: str, occurred_at: Any) -> None:
        """Accept an XP event (ADR-026)."""

    def _fire_xp_safely(self, fn: Any, **kwargs: Any) -> None:
        fn(**kwargs)


@pytest.fixture
def facade(monkeypatch: pytest.MonkeyPatch) -> _FakeFacade:
    """Patch every ``get_facade`` binding the pages and widgets use."""
    fake = _FakeFacade(MockCalDAVFacade())
    for module in (_state, _render, _table):
        monkeypatch.setattr(module, "get_facade", lambda: fake)
    return fake


class _EditorBrowser:
    """A model of the browser's data editor.

    The browser keeps the edits the user made, keyed by row position,
    and sends the whole accumulated state on every interaction. The
    state belongs to the component: a new element id means a new
    component, and the accumulated state is gone with it (ADR-034).
    """

    def __init__(self, key_prefix: str) -> None:
        self._key_prefix = key_prefix
        self._edits: dict[str, dict[str, Any]] = {}
        self._widget_id: str | None = None

    def widget_id(self, at: AppTest) -> str:
        """Return the element id of the table, as the browser sees it."""
        widget_id = _table_frame(at, self._key_prefix).proto.id
        if widget_id != self._widget_id:
            # A new component: the accumulated editing state is gone.
            self._edits = {}
            self._widget_id = widget_id
        return widget_id

    def edit(self, at: AppTest, row: int, changes: dict[str, Any]) -> AppTest:
        """Make an inline edit and send the accumulated state."""
        widget_id = self.widget_id(at)
        self._edits.setdefault(str(row), {}).update(changes)
        return _deliver(at, widget_id, self._edits)


def _table_frame(at: AppTest, key_prefix: str):
    """Return the rendered table whose key starts with the prefix."""
    return next(frame for frame in at.dataframe if (frame.key or "").startswith(key_prefix))


def _deliver(at: AppTest, widget_id: str, edited_rows: dict[str, dict[str, Any]]) -> AppTest:
    """Send an editing state to the table, as the browser would.

    ``AppTest`` does not model the editing state of a data editor,
    so the payload is handed to the runner as the widget state the
    browser would send. The state is addressed to the element id of
    the widget instance it belongs to.
    """
    states = WidgetStates()
    state = states.widgets.add()
    state.id = widget_id
    state.string_value = json.dumps({"edited_rows": edited_rows, "added_rows": [], "deleted_rows": []})
    return at._run(states)


def _task(facade: _FakeFacade, uid: str):
    """Return the task, or fail the test when it is gone."""
    task = facade.caldav.get_task(uid)
    assert task is not None, f"task {uid} is gone"
    return task


def _tasks_page() -> AppTest:
    """Render the tasks page."""
    at = AppTest.from_file(TASKS_APP)
    at.run()
    assert not at.exception, f"tasks page raised: {at.exception}"
    return at


def _series(facade: _FakeFacade, count: int = 3, title: str = "Pay rent"):
    """Create a recurrent task with ``count`` future occurrences."""
    return facade.caldav.create_recurrent_task(
        TaskCreate(title=title, assigned_date=date(2026, 1, 31)),
        rule=RecurrenceRule(
            frequency=RecurrenceFrequency.MONTHLY,
            interval=1,
            anchor=RecurrenceAnchor.ASSIGNED_DATE,
        ),
        materialized_future_count=count,
    )


def _occurrence_statuses(facade: _FakeFacade, series_uid: str) -> dict[int, TaskStatus]:
    """Return the status of every occurrence, keyed by its index."""
    return {task.recurrence_index or 0: task.status for task in facade.caldav.list_series_tasks(series_uid)}


# ---------------------------------------------------------------------------
# Widget identity
# ---------------------------------------------------------------------------


def test_the_widget_identity_follows_the_row_identity(facade: _FakeFacade) -> None:
    """The editor widget is replaced when the rendered rows change.

    The series keeps the row count stable while it replaces the row
    content, so the row count cannot be the identity of the widget.
    """
    _series(facade)
    at = _tasks_page()
    browser = _EditorBrowser("global_tasks")
    before = browser.widget_id(at)

    at = browser.edit(at, 0, {"Status": "completed"})
    assert not at.exception, f"tasks page raised: {at.exception}"

    assert browser.widget_id(at) != before


def test_the_widget_identity_is_stable_when_the_rows_do_not_change(facade: _FakeFacade) -> None:
    """A plain re-render keeps the same widget instance.

    The guard against a fix that replaces the grid on every render,
    which would break the inline edit flow.
    """
    _series(facade)
    at = _tasks_page()
    before = _table_frame(at, "global_tasks").proto.id

    at.run()
    assert not at.exception, f"tasks page raised: {at.exception}"

    assert _table_frame(at, "global_tasks").proto.id == before


# ---------------------------------------------------------------------------
# The stale payload
# ---------------------------------------------------------------------------


def test_a_stale_row_edit_is_not_applied_to_the_next_occurrence(facade: _FakeFacade) -> None:
    """A payload of a superseded row map is never dispatched.

    The user completes the current occurrence. The series renews, so
    the table still shows four rows, with the next occurrence in the
    first slot. A late payload of the superseded map must not
    complete that occurrence.
    """
    created = _series(facade)
    at = _tasks_page()
    browser = _EditorBrowser("global_tasks")
    superseded = browser.widget_id(at)

    at = browser.edit(at, 0, {"Status": "completed"})
    assert not at.exception, f"tasks page raised: {at.exception}"
    statuses = _occurrence_statuses(facade, created.series.uid)
    assert statuses[0] == TaskStatus.COMPLETED
    assert statuses[1] == TaskStatus.NEEDS_ACTION

    # The late payload, addressed to the widget of the superseded map.
    at = _deliver(at, superseded, {"0": {"Status": "completed"}})
    assert not at.exception, f"tasks page raised: {at.exception}"

    statuses = _occurrence_statuses(facade, created.series.uid)
    assert statuses[1] == TaskStatus.NEEDS_ACTION, "a stale payload completed the next occurrence"


def test_the_next_inline_edit_applies_only_to_its_own_row(facade: _FakeFacade) -> None:
    """The normal edit path still works after the rows changed.

    The browser's component was replaced, so its accumulated state is
    gone: the next edit carries only the cell the user touched.
    """
    created = _series(facade)
    at = _tasks_page()
    browser = _EditorBrowser("global_tasks")

    at = browser.edit(at, 0, {"Status": "completed"})
    assert not at.exception, f"tasks page raised: {at.exception}"

    at = browser.edit(at, 2, {"Status": "in-progress"})
    assert not at.exception, f"tasks page raised: {at.exception}"

    statuses = _occurrence_statuses(facade, created.series.uid)
    assert statuses[0] == TaskStatus.COMPLETED
    assert statuses[1] == TaskStatus.NEEDS_ACTION
    assert statuses[3] == TaskStatus.IN_PROGRESS


def test_an_ordinary_task_completion_still_works(facade: _FakeFacade) -> None:
    """The control case: an ordinary task leaves the table."""
    for title in ("One", "Two", "Three"):
        facade.caldav.create_task(TaskCreate(title=title))
    at = _tasks_page()
    browser = _EditorBrowser("global_tasks")

    at = browser.edit(at, 0, {"Status": "completed"})
    assert not at.exception, f"tasks page raised: {at.exception}"

    tasks = facade.caldav.list_tasks()
    assert tasks[0].status == TaskStatus.COMPLETED
    assert len(_table_frame(at, "global_tasks").value) == 2


# ---------------------------------------------------------------------------
# The other row-map changes
# ---------------------------------------------------------------------------


def test_a_re_sort_drops_the_pending_edit(facade: _FakeFacade) -> None:
    """A re-sort replaces the rows, so the pending edit is dropped.

    The user changes the priority of a row, a sort key. The frozen
    order (ADR-023) keeps the row in place, so the edit stays
    pending. The re-sort recomputes the order and moves that row: the
    pending edit must not land on the task that took over its slot.
    """
    low = facade.caldav.create_task(TaskCreate(title="Low", priority=1))
    high = facade.caldav.create_task(TaskCreate(title="High", priority=9))
    middle = facade.caldav.create_task(TaskCreate(title="Middle", priority=5))
    at = _tasks_page()
    browser = _EditorBrowser("global_tasks")

    # The table is sorted by priority, descending: High, Middle, Low.
    at = browser.edit(at, 0, {"Priority": 1})
    assert not at.exception, f"tasks page raised: {at.exception}"
    assert _task(facade, high.uid).priority == 1

    # The re-sort moves the edited row: the order is now Middle, Low, High.
    at.button(key="global_tasks__resort_btn").click().run()
    assert not at.exception, f"tasks page raised: {at.exception}"

    at = browser.edit(at, 2, {"Status": "in-progress"})
    assert not at.exception, f"tasks page raised: {at.exception}"

    assert _task(facade, middle.uid).priority == 5, "the pending edit was re-dispatched"
    assert _task(facade, high.uid).priority == 1
    assert _task(facade, high.uid).status == TaskStatus.IN_PROGRESS
    assert _task(facade, low.uid).priority == 1


def test_a_filter_change_drops_the_pending_edit(facade: _FakeFacade) -> None:
    """A filter change replaces the rows, so the pending edit is dropped.

    The filter swaps one task for another, so the row count stays the
    same and the row count cannot be the identity of the widget.
    """
    first = facade.caldav.create_task(TaskCreate(title="First", priority=5))
    second = facade.caldav.create_task(TaskCreate(title="Second", priority=5))
    third = facade.caldav.create_task(TaskCreate(title="Third", priority=5))
    done = facade.caldav.create_task(TaskCreate(title="Done", priority=5))
    facade.caldav.update_task(second.uid, TaskUpdate(status=TaskStatus.IN_PROGRESS))
    facade.caldav.update_task(done.uid, TaskUpdate(status=TaskStatus.COMPLETED))
    at = _tasks_page()
    browser = _EditorBrowser("global_tasks")

    # The filter shows the needs-action and the in-progress tasks.
    at = browser.edit(at, 1, {"Priority": 9})
    assert not at.exception, f"tasks page raised: {at.exception}"
    assert _task(facade, second.uid).priority == 9

    # The filter now shows the needs-action and the completed tasks:
    # the second task leaves, the completed one takes its slot.
    at.pills[0].set_value([TaskStatus.NEEDS_ACTION.value, TaskStatus.COMPLETED.value]).run()
    assert not at.exception, f"tasks page raised: {at.exception}"

    at = browser.edit(at, 2, {"Title": "Edited"})
    assert not at.exception, f"tasks page raised: {at.exception}"

    assert _task(facade, third.uid).priority == 5, "the pending edit was re-dispatched"
    assert _task(facade, second.uid).priority == 9
    assert _task(facade, done.uid).title == "Edited"
    assert _task(facade, first.uid).priority == 5


# ---------------------------------------------------------------------------
# The other editable tables
# ---------------------------------------------------------------------------


def test_the_projects_table_and_the_journal_table_bind_their_rows(facade: _FakeFacade) -> None:
    """The projects and the journal tables bind their rows as well.

    A row that is replaced by another one keeps the row count, so the
    widget identity has to follow the rows, not the count.
    """
    alpha = facade.caldav.create_project(ProjectCreate(name="Alpha"))
    facade.caldav.create_project(ProjectCreate(name="Beta"))
    at = AppTest.from_string(PROJECTS_APP)
    at.run()
    assert not at.exception, f"projects app raised: {at.exception}"
    before = _table_frame(at, "identity_projects").proto.id

    facade.caldav.delete_project(alpha.uid)
    facade.caldav.create_project(ProjectCreate(name="Gamma"))
    at.run()
    assert not at.exception, f"projects app raised: {at.exception}"

    assert _table_frame(at, "identity_projects").proto.id != before

    facade.journal.entries.append(Entry(date=date(2026, 9, 5), title="First", content="body"))
    at = AppTest.from_string(JOURNAL_APP)
    at.run()
    assert not at.exception, f"journal app raised: {at.exception}"
    before = _table_frame(at, "identity_journal").proto.id

    facade.journal.entries.clear()
    facade.journal.entries.append(Entry(date=date(2026, 9, 6), title="Second", content="body"))
    at.run()
    assert not at.exception, f"journal app raised: {at.exception}"

    assert _table_frame(at, "identity_journal").proto.id != before
