"""Unit tests for the calendar page layout and day grouping.

The calendar page is a Streamlit script, so the tests drive it with
Streamlit's built-in ``AppTest``: the real page module runs against a
fake CalDAV facade and the assertions read the rendered widget tree.
That keeps the narrow-screen structure checkable without a phone and
without adding a browser-test dependency.

Covered:

- the week grid is one seven-column block where each day owns its
  heading and its events, so a stacked (narrow) layout cannot
  separate a heading from the events it labels;
- the navigation is a single row of buttons with no nested column
  group (nested columns shrink-wrap and squeeze the labels);
- ``Today`` / ``Prev`` / ``Next`` move the displayed week;
- the responsive style stays scoped to the calendar grid;
- events are grouped by their local date, not by their UTC date.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import Block, Column

from genesis.entities import Event, EventStatus, Task
from genesis.ui import _state

APP_SCRIPT = Path(__file__).parent / "_calendar_page_app.py"
WEEK_GRID_KEY = "calendar_week_grid"

# Wednesday 2026-06-10; the week grid starts on Monday 2026-06-08.
WEEK_ANCHOR = date(2026, 6, 10)


class _FakeCalDAV:
    """CalDAV stand-in that serves a fixed list of events."""

    def __init__(self, events: list[Event]) -> None:
        self.events = list(events)

    def list_events(
        self,
        *,
        status: EventStatus | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Event]:
        """Overlap filter, mirroring the RFC 4791 time-range semantics."""
        found = []
        for event in self.events:
            if status is not None and event.status != status:
                continue
            if start is not None and event.end <= start:
                continue
            if end is not None and event.start >= end:
                continue
            found.append(event)
        return found

    def list_tasks(self, *, start: datetime | None = None, end: datetime | None = None) -> list[Task]:
        return []


class _FakeFacade:
    def __init__(self, events: list[Event]) -> None:
        self.caldav = _FakeCalDAV(events)


@pytest.fixture
def fake_facade(monkeypatch: pytest.MonkeyPatch) -> _FakeFacade:
    """Patch ``get_facade`` so the page renders without a real Radicale."""
    facade = _FakeFacade([])
    monkeypatch.setattr(_state, "get_facade", lambda: facade)
    return facade


@pytest.fixture
def adelaide_clock() -> Iterator[None]:
    """Run the test with the machine clock in Australia/Adelaide.

    The conftest pins ``$TZ`` to UTC; the day-grouping bug only shows
    in a timezone that is ahead of UTC, so the clock is moved to
    Adelaide (UTC+9:30 in June) for the duration of the test.
    """
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Australia/Adelaide"
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


def _run(facade: _FakeFacade, *, week: date | None = None) -> AppTest:
    """Render the calendar page and return the finished app test."""
    at = AppTest.from_file(APP_SCRIPT)
    if week is not None:
        at.session_state["cal_week"] = week
    at.run()
    assert not at.exception, f"calendar page raised: {at.exception}"
    return at


def _user_key(node: Block) -> str | None:
    """Read the user key of a block the way the frontend does.

    Streamlit encodes the key in the block id as
    ``<type>-<hash>-<user key>`` and the frontend keeps everything
    after the first two dash-separated parts.
    """
    raw = getattr(node.proto, "id", "") or ""
    if not raw:
        return None
    return "-".join(raw.split("-")[2:]) or None


def _find_keyed_block(root: Block, key: str) -> Block:
    for node in root:
        if isinstance(node, Block) and _user_key(node) == key:
            return node
    raise AssertionError(f"no block with key {key!r} in the rendered page")


def _day_columns(at: AppTest) -> list[Column]:
    """The seven day columns of the week grid."""
    grid = _find_keyed_block(at.main, WEEK_GRID_KEY)
    columns = list(grid.columns)
    assert len(columns) == 7, f"expected 7 day columns, got {len(columns)}"
    return columns


def _column_text(column: Column) -> str:
    """All rendered text of a day column, headings and events alike."""
    parts = [markdown.value for markdown in column.markdown]
    parts += [caption.value for caption in column.caption]
    return "\n".join(parts)


def _heading(column: Column) -> str:
    markdowns = list(column.markdown)
    assert markdowns, "day column has no heading"
    return markdowns[0].value


def _monday(week: date) -> date:
    return week - timedelta(days=week.weekday())


def test_week_grid_has_one_self_contained_column_per_day(fake_facade: _FakeFacade) -> None:
    """Each day column holds its own heading and its own events."""
    fake_facade.caldav.events = [
        Event(
            uid="e-mon",
            summary="Monday review",
            start=datetime(2026, 6, 8, 1, 0, tzinfo=UTC),
            end=datetime(2026, 6, 8, 2, 0, tzinfo=UTC),
            status=EventStatus.CONFIRMED,
        ),
        Event(
            uid="e-wed",
            summary="Wednesday review",
            start=datetime(2026, 6, 10, 9, 0, tzinfo=UTC),
            end=datetime(2026, 6, 10, 10, 0, tzinfo=UTC),
            status=EventStatus.CONFIRMED,
        ),
    ]

    at = _run(fake_facade, week=WEEK_ANCHOR)
    columns = _day_columns(at)
    monday = _monday(WEEK_ANCHOR)

    for index, column in enumerate(columns):
        day = monday + timedelta(days=index)
        heading = _heading(column)
        assert day.strftime("%a") in heading, f"column {index} misses the {day} heading"
        assert str(day.day) in heading, f"column {index} misses the day number of {day}"

    # The events live in the column of their own day, nowhere else.
    assert "**Monday review**" in _column_text(columns[0])
    assert "**Wednesday review**" in _column_text(columns[2])
    assert "**Monday review**" not in _column_text(columns[2])
    assert "**Wednesday review**" not in _column_text(columns[0])

    # Days without events keep the empty-day dash.
    assert "—" in _column_text(columns[1])


def test_navigation_is_a_single_row_of_buttons(fake_facade: _FakeFacade) -> None:
    """The navigation holds three buttons and no nested column group."""
    at = _run(fake_facade, week=WEEK_ANCHOR)

    nav_columns = [node for node in at.main if isinstance(node, Column) and list(node.button)]
    assert len(nav_columns) == 1, "the navigation must sit in exactly one column"

    nav = nav_columns[0]
    buttons = list(nav.button)
    assert [button.key for button in buttons] == ["cal_today", "cal_prev", "cal_next"]
    assert [button.label for button in buttons] == ["Today", "◀ Prev", "Next ▶"]

    nested = [column for column in nav.columns if column is not nav]
    assert nested == [], "the navigation must not nest another column group"


def test_next_and_prev_move_the_week_by_seven_days(fake_facade: _FakeFacade) -> None:
    """The navigation buttons shift the displayed week."""
    at = _run(fake_facade, week=WEEK_ANCHOR)
    start = at.session_state["cal_week"]

    at.button(key="cal_next").click().run()
    assert not at.exception, f"calendar page raised: {at.exception}"
    assert at.session_state["cal_week"] == start + timedelta(days=7)

    at.button(key="cal_prev").click().run()
    assert not at.exception, f"calendar page raised: {at.exception}"
    assert at.session_state["cal_week"] == start


def test_today_returns_to_the_current_week(fake_facade: _FakeFacade) -> None:
    """The Today button jumps back to the current week."""
    at = _run(fake_facade, week=WEEK_ANCHOR)
    assert at.session_state["cal_week"] != date.today()

    at.button(key="cal_today").click().run()
    assert not at.exception, f"calendar page raised: {at.exception}"
    assert at.session_state["cal_week"] == date.today()


def test_responsive_style_is_scoped_to_the_calendar_grid(fake_facade: _FakeFacade) -> None:
    """The page ships one scoped style that keeps the week row scrollable."""
    at = _run(fake_facade, week=WEEK_ANCHOR)

    bodies = []
    for element in at.get("html"):
        proto = getattr(element, "proto", None)
        body = getattr(proto, "body", None)
        if isinstance(body, str):
            bodies.append(body)
    style = "\n".join(bodies)

    assert f".st-key-{WEEK_GRID_KEY}" in style
    assert "@media (min-width: 641px)" in style
    assert "overflow-x: auto" in style
    assert 'data-testid="stHorizontalBlock"' in style
    assert 'data-testid="stColumn"' in style


def test_early_morning_event_is_grouped_under_its_local_date(
    fake_facade: _FakeFacade,
    adelaide_clock: None,
) -> None:
    """Regression: an event is grouped by its local date, not its UTC date.

    2026-06-09 23:30 UTC is 2026-06-10 09:00 in Adelaide. Grouping by
    the UTC date filed the event under Tuesday instead of Wednesday.
    """
    fake_facade.caldav.events = [
        Event(
            uid="e-morning",
            summary="Morning standup",
            start=datetime(2026, 6, 9, 23, 30, tzinfo=UTC),
            end=datetime(2026, 6, 10, 0, 0, tzinfo=UTC),
            status=EventStatus.CONFIRMED,
        ),
    ]

    at = _run(fake_facade, week=WEEK_ANCHOR)
    columns = _day_columns(at)
    monday = _monday(WEEK_ANCHOR)

    tuesday = columns[(date(2026, 6, 9) - monday).days]
    wednesday = columns[(date(2026, 6, 10) - monday).days]

    assert "**Morning standup**" in _column_text(wednesday)
    assert "09:00–09:30" in _column_text(wednesday)
    assert "**Morning standup**" not in _column_text(tuesday)
