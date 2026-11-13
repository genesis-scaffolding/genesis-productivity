"""Headless UI checks for the task detail view and task-linked journals.

The Tasks and Journal pages are Streamlit scripts, so the tests drive
them with Streamlit's built-in ``AppTest``: the real page module runs
against a fake facade and the assertions read the rendered widget
tree. That keeps the manual acceptance checks for this feature
honest without a browser.

Covered:

- the shared task table's Open action routes to the task detail view;
- the detail view renders the task, its metadata, and the journal
  entries linked to it (of any entry type);
- the Back button returns to the list, and a missing task shows a
  not-found state;
- the add-entry dialog preselects the task and the ``task`` type, and
  the shared create form links the new entry to that task;
- the journal entry view shows the linked task and the edit form keeps
  the link;
- a table row action never navigates from inside its callback, which
  Streamlit treats as a no-op.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest
import streamlit as st
from streamlit.proto.WidgetStates_pb2 import WidgetStates
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest

from genesis.caldav.facade import CalDAVFacade
from genesis.entities import ProjectCreate, TaskCreate, TaskStatus, TaskUpdate
from genesis.journal.facade import Entry, EntryType, JournalFacade
from genesis.ui import _render, _state, _table

from .mock_caldav import MockCalDAVFacade

TASKS_APP = Path(__file__).parent / "_tasks_page_app.py"
JOURNAL_APP = Path(__file__).parent / "_journal_page_app.py"
JOURNAL_FORM_APP = Path(__file__).parent / "_journal_form_app.py"
JOURNAL_DIALOG_APP = Path(__file__).parent / "_journal_dialog_app.py"

# Mirrors ``genesis.ui._table._PENDING_PAGE_SWITCH_KEY``.
_PENDING_PAGE_SWITCH_KEY = "_pending_page_switch"

TASK_TITLE = "Ship the thing"


class _FakeFacade:
    """Facade stand-in: a mock CalDAV plus a real on-disk journal."""

    def __init__(self, journal_path: Path, caldav: MockCalDAVFacade) -> None:
        self.caldav = caldav
        self.journal = JournalFacade(journal_path)

    def list_series_summaries(self):
        """Return the summary row of every recurrence series."""
        from genesis.caldav import recurrence

        return recurrence.series_summaries(self.caldav._recurrence_store())


@pytest.fixture
def fake_facade(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _FakeFacade:
    """Patch every ``get_facade`` binding the pages and widgets use."""
    caldav = MockCalDAVFacade()
    project = caldav.create_project(ProjectCreate(name="Alpha"))
    task = caldav.create_task(
        TaskCreate(
            title=TASK_TITLE,
            description="Finish the feature.",
            project_id=project.uid,
            priority=7,
            hard_deadline=datetime(2026, 9, 30, 12, 0, tzinfo=UTC),
            scheduled_start=datetime(2026, 9, 5, 9, 0, tzinfo=UTC),
            scheduled_duration=45,
        )
    )
    task.status = TaskStatus.IN_PROGRESS
    task.assigned_date = date(2026, 9, 5)
    task.completed_at = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)

    facade = _FakeFacade(tmp_path / "journal", caldav)
    for module in (_state, _render, _table):
        monkeypatch.setattr(module, "get_facade", lambda: facade)
    return facade


def _task_uid(facade: _FakeFacade) -> str:
    return facade.caldav.list_tasks()[0].uid


def _run_tasks_app(monkeypatch: pytest.MonkeyPatch, uid: str | None = None) -> AppTest:
    """Render the Tasks page, optionally in its detail state."""
    if uid is None:
        monkeypatch.delenv("GENESIS_TEST_TASK_UID", raising=False)
    else:
        monkeypatch.setenv("GENESIS_TEST_TASK_UID", uid)
    at = AppTest.from_file(TASKS_APP)
    at.run()
    assert not at.exception, f"tasks page raised: {at.exception}"
    return at


def _texts(at: AppTest) -> str:
    """All rendered text of the page, as one blob."""
    parts = [element.value for element in at.markdown]
    parts += [element.value for element in at.caption]
    parts += [element.value for element in at.title]
    parts += [element.value for element in at.subheader]
    return "\n".join(parts)


def _switch_page_recorder(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, str]]]:
    """Capture ``st.switch_page`` calls instead of navigating."""
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_switch_page(page: str, *, query_params: dict[str, str] | None = None) -> None:
        calls.append((page, dict(query_params or {})))

    monkeypatch.setattr(st, "switch_page", fake_switch_page)
    return calls


def _click_table_action(at: AppTest, *, row: int, label: str) -> AppTest:
    """Click a per-row action button of the first table that has one.

    ``AppTest`` does not model the ``ButtonColumn`` of a data editor,
    so the click is handed to the runner as the widget state the
    browser would send. ``AppTest._run`` is the only door into that
    state; it re-runs the app with the given widget states.
    """
    frame = next(f for f in at.dataframe if "actions" in f.proto.button_click_widgets)
    widget_id = frame.proto.button_click_widgets["actions"]
    widget_states = WidgetStates()
    state = widget_states.widgets.add()
    state.id = widget_id
    state.string_trigger_value.data = json.dumps({"row": row, "label": label})
    return at._run(widget_states)


def _app_with_stub_pages(tmp_path: Path) -> Path:
    """Copy the tasks page app next to stub pages it can switch to.

    ``st.switch_page`` resolves its target relative to the main script
    and only finds the pages the app registered, so the test app needs
    real pages to navigate to.
    """
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "journal.py").write_text("import streamlit as st\nst.title('Journal entry page')\n", encoding="utf-8")
    (pages / "tasks.py").write_text("import streamlit as st\nst.title('Tasks page')\n", encoding="utf-8")
    app = tmp_path / "main.py"
    app.write_text(TASKS_APP.read_text(encoding="utf-8"), encoding="utf-8")
    return app


@pytest.fixture
def registered_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the app's page registry alive across AppTest runs.

    ``AppTest`` builds a fresh pages manager per run, so the pages the
    app registered in an earlier run are unknown while a widget
    callback runs (the callback phase runs before the script body).
    Sharing the manager keeps the registry, which is what lets the
    test drive a real ``st.switch_page`` and see what Streamlit does
    with it.
    """
    real_pages_manager = PagesManager
    managers: dict[str, object] = {}

    def factory(*args, **kwargs):  # type: ignore[no-untyped-def]
        key = str(args[0])
        if key not in managers:
            # ``AppTest`` resets the mode flag on the name it holds, which
            # is this factory once patched, so the real class keeps a
            # stale value. Reset it here so the manager works the mode
            # out from the app's own directory.
            real_pages_manager.uses_pages_directory = None
            managers[key] = real_pages_manager(*args, **kwargs)
        return managers[key]

    monkeypatch.setattr("streamlit.testing.v1.app_test.PagesManager", factory)


@pytest.fixture(autouse=True)
def _no_pending_page_switch() -> Iterator[None]:
    """Keep a pending page switch from leaking between tests.

    Outside a Streamlit run the session state is a process-wide
    fallback, so a switch a test asked for but never ran would be
    picked up by the next test. The key mirrors
    ``genesis.ui._table._PENDING_PAGE_SWITCH_KEY``.
    """
    st.session_state.pop(_PENDING_PAGE_SWITCH_KEY, None)
    yield
    st.session_state.pop(_PENDING_PAGE_SWITCH_KEY, None)


def _button_by_label(at: AppTest, label: str):
    """Return the button with the given label."""
    for button in at.button:
        if button.label == label:
            return button
    raise AssertionError(f"no button labelled {label!r}")


def _text_input_by_label(at: AppTest, label: str):
    """Return the text input with the given label."""
    for field in at.text_input:
        if field.label == label:
            return field
    raise AssertionError(f"no text input labelled {label!r}")


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_task_table_open_action_switches_to_the_task_detail(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Open action leaves the current page and lands on /tasks?uid=.

    The switch is deferred to the script body: a widget callback cannot
    navigate, ``st.switch_page`` there is a no-op and Streamlit warns
    "Calling st.rerun() within a callback is a no-op.".
    """
    calls = _switch_page_recorder(monkeypatch)
    task = fake_facade.caldav.list_tasks()[0]

    _table.handle_task_action("Open", task, caldav=cast(CalDAVFacade, fake_facade.caldav))
    assert calls == [], "the row-action callback must not navigate"

    _table.run_pending_page_switch()

    assert calls == [("pages/tasks.py", {"uid": task.uid})]


def test_task_detail_journal_open_action_switches_to_the_journal_page(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The journal table's Open action lands on /journal?uid=.

    Like the task table's Open action, the switch is deferred to the
    script body.
    """
    calls = _switch_page_recorder(monkeypatch)

    _table.request_journal_entry("j-123")
    assert calls == [], "the row-action callback must not navigate"

    _table.run_pending_page_switch()

    assert calls == [("pages/journal.py", {"uid": "j-123"})]


# ---------------------------------------------------------------------------
# Row actions: no navigation from inside a callback
# ---------------------------------------------------------------------------


def test_task_detail_journal_open_action_navigates_without_the_callback_warning(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    registered_pages: None,
) -> None:
    """The journal Open action lands on the journal page, warning-free.

    A row action runs inside a widget callback, and Streamlit refuses
    to navigate from there: ``st.switch_page`` raises the rerun
    exception inside the callback, so the queued navigation is dropped
    and Streamlit warns "Calling st.rerun() within a callback is a
    no-op.". The action records the target instead, and the table
    switches the page from the script body.
    """
    task_uid = _task_uid(fake_facade)
    entry = Entry(date=date(2026, 9, 5), type=EntryType.TASK, title="linked", content="body", task_id=task_uid)
    fake_facade.journal.save_entry(entry)

    monkeypatch.setenv("GENESIS_TEST_TASK_UID", task_uid)
    at = AppTest.from_file(_app_with_stub_pages(tmp_path))
    at.run()
    assert not at.exception, f"tasks page raised: {at.exception}"

    at = _click_table_action(at, row=0, label="Open")

    assert not at.exception, f"tasks page raised: {at.exception}"
    warnings = [w.value for w in at.warning]
    assert not [w for w in warnings if "no-op" in w], f"Streamlit warned: {warnings}"
    assert "Journal entry page" in _texts(at)


def test_task_list_open_action_navigates_without_the_callback_warning(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    registered_pages: None,
) -> None:
    """The task table's Open action lands on the task page, warning-free.

    Same callback rule as the journal table's Open action: the switch
    runs from the script body, not from the row-action callback.
    """
    monkeypatch.delenv("GENESIS_TEST_TASK_UID", raising=False)
    at = AppTest.from_file(_app_with_stub_pages(tmp_path))
    at.run()
    assert not at.exception, f"tasks page raised: {at.exception}"

    at = _click_table_action(at, row=0, label="Open")

    assert not at.exception, f"tasks page raised: {at.exception}"
    warnings = [w.value for w in at.warning]
    assert not [w for w in warnings if "no-op" in w], f"Streamlit warned: {warnings}"
    assert "Tasks page" in _texts(at)


def test_task_detail_journal_delete_action_opens_the_confirm_dialog(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Delete action still opens the confirmation dialog.

    The dialog opens from the row-action callback, which is allowed:
    only navigating from a callback is a no-op.
    """
    task_uid = _task_uid(fake_facade)
    entry = Entry(date=date(2026, 9, 5), type=EntryType.TASK, title="linked", content="body", task_id=task_uid)
    fake_facade.journal.save_entry(entry)

    at = _run_tasks_app(monkeypatch, task_uid)
    at = _click_table_action(at, row=0, label="Delete")

    assert not at.exception, f"tasks page raised: {at.exception}"
    keys = [button.key for button in at.button]
    assert "delcfm_entry 2026-09-05" in keys
    assert "delcn_entry 2026-09-05" in keys


# ---------------------------------------------------------------------------
# Tasks page: list and detail
# ---------------------------------------------------------------------------


def test_tasks_page_shows_the_list_by_default(fake_facade: _FakeFacade, monkeypatch: pytest.MonkeyPatch) -> None:
    at = _run_tasks_app(monkeypatch)
    assert "Tasks" in _texts(at)
    assert "Journal Entries" not in _texts(at)


def test_task_detail_renders_the_task_and_its_metadata(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _run_tasks_app(monkeypatch, _task_uid(fake_facade))
    texts = _texts(at)

    assert "Productivity > Tasks" in texts
    assert TASK_TITLE in texts
    assert "Finish the feature." in texts
    assert "Journal Entries" in texts
    # Read-only metadata.
    assert "Alpha" in texts
    assert "7" in texts
    assert "2026-09-05" in texts
    assert "45 min" in texts


def test_task_detail_lists_the_journal_entries_linked_to_the_task(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_uid = _task_uid(fake_facade)
    fake_facade.journal.save_entry(
        Entry(date=date(2026, 9, 5), type=EntryType.TASK, title="linked", content="body", task_id=task_uid)
    )

    at = _run_tasks_app(monkeypatch, task_uid)

    tables = [frame.value for frame in at.dataframe]
    assert any("linked" in str(frame) for frame in tables)


def test_task_detail_lists_entries_of_any_type(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The link is the relationship, not the type: a daily note that
    # points at the task belongs in the task's journal table.
    task_uid = _task_uid(fake_facade)
    fake_facade.journal.save_entry(
        Entry(date=date(2026, 9, 5), type=EntryType.DAILY, title="daily-note", content="body", task_id=task_uid)
    )

    at = _run_tasks_app(monkeypatch, task_uid)

    tables = [frame.value for frame in at.dataframe]
    assert any("daily-note" in str(frame) for frame in tables)


def test_task_detail_omits_entries_linked_to_other_tasks(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_facade.journal.save_entry(
        Entry(date=date(2026, 9, 5), type=EntryType.TASK, title="other", content="body", task_id="t-000000")
    )

    at = _run_tasks_app(monkeypatch, _task_uid(fake_facade))

    tables = [frame.value for frame in at.dataframe]
    assert not any("other" in str(frame) for frame in tables)


def test_task_detail_back_button_returns_to_the_list(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _run_tasks_app(monkeypatch, _task_uid(fake_facade))
    assert "Journal Entries" in _texts(at)

    at.button(key="td_back").click().run()
    assert not at.exception, f"tasks page raised: {at.exception}"

    texts = _texts(at)
    assert "Journal Entries" not in texts
    assert "Your global backlog and scheduled work." in texts


def test_task_detail_missing_task_shows_not_found(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _run_tasks_app(monkeypatch, "t-does-not-exist")
    assert at.error
    assert "Task not found." in at.error[0].value


# ---------------------------------------------------------------------------
# Adding a journal entry from the task detail view
# ---------------------------------------------------------------------------


def test_add_journal_entry_dialog_preselects_the_task_and_type(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_uid = _task_uid(fake_facade)
    at = _run_tasks_app(monkeypatch, task_uid)

    at.button(key=f"td_add_journal_{task_uid}").click().run()
    assert not at.exception, f"tasks page raised: {at.exception}"

    selects = {box.label: box.value for box in at.selectbox}
    assert selects["Type"] == "task"
    assert selects["Task"] == f"{TASK_TITLE} ({task_uid})"


def test_create_dialog_resets_the_type_default_on_each_open(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reopening the create dialog starts from the requested defaults.

    A keyed ``st.selectbox`` ignores its ``index`` once the key exists in
    session state, so a stale pick (e.g. ``task`` left behind by a prior
    open with ``default_type="task"``) survives into the next open unless
    the wrapper resets it. The leak is modelled by seeding the persisted
    type key directly (AppTest drops a keyed widget's value on dialog
    close, so it cannot be reproduced by closing and reopening), then
    reopening with a different default: the requested default must win.
    """
    type_key = f"{_render._JOURNAL_DIALOG_KEY_PREFIX}_type"
    monkeypatch.setenv("GENESIS_TEST_DIALOG_TYPE", "daily")

    at = AppTest.from_file(JOURNAL_DIALOG_APP)
    at.run()
    assert not at.exception, f"dialog app raised: {at.exception}"

    # A prior open with default_type="task" left the type key behind.
    at.session_state[type_key] = "task"
    at.button(key="open_create_dialog").click().run()
    assert not at.exception, f"dialog app raised: {at.exception}"

    selects = {box.label: box.value for box in at.selectbox}
    assert selects["Type"] == "daily"


def test_journal_entry_created_from_the_task_detail_is_linked(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared create form links the new entry to the preselected task.

    This is the call the create dialog makes, with the defaults the
    task detail page passes in.
    """
    task_uid = _task_uid(fake_facade)
    monkeypatch.setenv("GENESIS_TEST_JOURNAL_UID", "j-formtest")
    monkeypatch.setenv("GENESIS_TEST_JOURNAL_TYPE", "task")
    monkeypatch.setenv("GENESIS_TEST_JOURNAL_TASK_UID", task_uid)

    at = AppTest.from_file(JOURNAL_FORM_APP)
    at.run()
    assert not at.exception, f"journal form raised: {at.exception}"

    at.text_input(key="jf_title").set_value("fresh note")
    at.text_area(key="jf_body").set_value("body text")
    _button_by_label(at, "Create").click().run()
    assert not at.exception, f"journal form raised: {at.exception}"

    entries = fake_facade.journal.list_entries(task_id=task_uid)
    assert len(entries) == 1
    assert entries[0].title == "fresh note"
    assert entries[0].type == EntryType.TASK
    assert entries[0].task_id == task_uid


def test_journal_page_create_view_can_link_a_task(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Journal page's own create form links the entry to a task."""
    task_uid = _task_uid(fake_facade)
    monkeypatch.setenv("GENESIS_TEST_JOURNAL_UID", "j-new-entry")
    monkeypatch.setenv("GENESIS_TEST_JOURNAL_NEW", "1")

    at = AppTest.from_file(JOURNAL_APP)
    at.run()
    assert not at.exception, f"journal page raised: {at.exception}"

    at.selectbox(key="je_create_j-new-entry_task").set_value(f"{TASK_TITLE} ({task_uid})")
    at.text_input(key="je_create_j-new-entry_title").set_value("page note")
    at.text_area(key="je_create_j-new-entry_body").set_value("body text")
    _button_by_label(at, "Create").click().run()
    assert not at.exception, f"journal page raised: {at.exception}"

    entries = fake_facade.journal.list_entries(task_id=task_uid)
    assert len(entries) == 1
    assert entries[0].title == "page note"
    assert entries[0].task_id == task_uid


def test_task_rename_keeps_the_journal_entry_linked(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_uid = _task_uid(fake_facade)
    fake_facade.journal.save_entry(
        Entry(date=date(2026, 9, 5), type=EntryType.TASK, title="linked", content="body", task_id=task_uid)
    )
    fake_facade.caldav.update_task(task_uid, TaskUpdate(title="Renamed task"))

    at = _run_tasks_app(monkeypatch, task_uid)

    tables = [frame.value for frame in at.dataframe]
    assert any("linked" in str(frame) for frame in tables)


# ---------------------------------------------------------------------------
# Journal page
# ---------------------------------------------------------------------------


def test_journal_entry_view_shows_the_linked_task(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_uid = _task_uid(fake_facade)
    entry = Entry(date=date(2026, 9, 5), type=EntryType.TASK, title="linked", content="body", task_id=task_uid)
    fake_facade.journal.save_entry(entry)

    monkeypatch.setenv("GENESIS_TEST_JOURNAL_UID", entry.uid)
    at = AppTest.from_file(JOURNAL_APP)
    at.run()
    assert not at.exception, f"journal page raised: {at.exception}"

    assert f"Task: {TASK_TITLE} ({task_uid})" in _texts(at)


def test_journal_entry_view_shows_a_missing_task(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = Entry(date=date(2026, 9, 5), type=EntryType.TASK, title="linked", content="body", task_id="t-000000")
    fake_facade.journal.save_entry(entry)

    monkeypatch.setenv("GENESIS_TEST_JOURNAL_UID", entry.uid)
    at = AppTest.from_file(JOURNAL_APP)
    at.run()
    assert not at.exception, f"journal page raised: {at.exception}"

    assert "Task: t-000000 (missing)" in _texts(at)


def test_journal_edit_form_keeps_the_task_link(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_uid = _task_uid(fake_facade)
    entry = Entry(date=date(2026, 9, 5), type=EntryType.TASK, title="linked", content="body", task_id=task_uid)
    fake_facade.journal.save_entry(entry)

    monkeypatch.setenv("GENESIS_TEST_JOURNAL_UID", entry.uid)
    at = AppTest.from_file(JOURNAL_APP)
    at.run()
    assert not at.exception, f"journal page raised: {at.exception}"

    at.button(key="jev_edit_" + entry.uid).click().run()
    assert not at.exception, f"journal page raised: {at.exception}"

    selects = {box.label: box.value for box in at.selectbox}
    assert selects["Task"] == f"{TASK_TITLE} ({task_uid})"

    _text_input_by_label(at, "Title").set_value("renamed")
    _button_by_label(at, "Save").click().run()
    assert not at.exception, f"journal page raised: {at.exception}"

    reloaded = fake_facade.journal.get_entry(entry.uid)
    assert reloaded is not None
    assert reloaded.title == "renamed"
    assert reloaded.task_id == task_uid


def test_journal_list_offers_the_task_type(
    fake_facade: _FakeFacade,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GENESIS_TEST_JOURNAL_UID", raising=False)
    at = AppTest.from_file(JOURNAL_APP)
    at.run()
    assert not at.exception, f"journal page raised: {at.exception}"

    options = []
    for group in at.pills:
        options.extend(option for option in group.options)
    assert "task" in options
