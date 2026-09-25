"""Unit tests for ``genesis.ui.pages.focus._maybe_fire_milestone``.

Drives the milestone-dedup logic directly without bringing up
the Streamlit runtime. The focus module binds ``facade`` at
import time via ``get_facade()``; we patch that helper before
import so the module-level reference points at a fake facade we
control.

Covers the ADR-028 behaviour:

- First crossing at minute 30 fires, persists the field.
- A second render within the same page load does not refire.
- Page refresh scenario: back-fire all crossings since ``last``.
- Steady state: in-memory cache suppresses redundant PUTs.
- PUT failure with another device's win: refetch, skip silently.
- PUT transient failure: skip this tick's notify, allow retry.
- Task-aligned sessions include the task title.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

import pytest

from genesis.entities import (
    TimeLogEntry,
    TimeLogEntryUpdate,
    TimeLogMode,
    TimeLogStatus,
)


class _FakeCaldav:
    """Minimal CalDAV facade for milestone-dedup tests."""

    def __init__(self) -> None:
        self.entries: dict[str, TimeLogEntry] = {}
        self.updates: list[tuple[str, TimeLogEntryUpdate]] = []
        self.gets: list[str] = []
        # "ok" returns the entry on update; "fail" returns None.
        self.put_mode = "ok"

    def update_time_log_entry(self, uid: str, data: TimeLogEntryUpdate):
        self.updates.append((uid, data))
        if self.put_mode == "fail":
            return None
        entry = self.entries.get(uid)
        if entry is None:
            return None
        for key, value in data.model_dump(exclude_unset=True).items():
            if value is not None:
                setattr(entry, key, value)
        return entry

    def get_time_log_entry(self, uid: str):
        self.gets.append(uid)
        return self.entries.get(uid)


class _FakeNotify:
    def __init__(self) -> None:
        self.sends: list[tuple[str, str, list[str]]] = []

    def send(self, title: str, message: str, *, tags: list[str] | None = None) -> None:
        self.sends.append((title, message, list(tags or [])))


class _FakeFacade:
    def __init__(self) -> None:
        self.caldav = _FakeCaldav()
        self.notify = _FakeNotify()


def _make_session(
    *,
    uid: str,
    last_milestone_minutes: int = 0,
    task_id: str | None = None,
) -> TimeLogEntry:
    return TimeLogEntry(
        uid=uid,
        label="Test session",
        start=datetime(2026, 9, 9, 21, 0, 0, tzinfo=UTC),
        mode=TimeLogMode.COUNTUP,
        status=TimeLogStatus.IN_PROGRESS,
        task_id=task_id,
        last_milestone_minutes=last_milestone_minutes,
    )


@pytest.fixture
def focus(monkeypatch: pytest.MonkeyPatch):
    """Return (focus_module, fake) with the focus module freshly
    imported against the patched facade."""
    fake = _FakeFacade()
    monkeypatch.setattr("genesis.ui._state.get_facade", lambda: fake)
    sys.modules.pop("genesis.ui.pages.focus", None)
    import genesis.ui.pages.focus as focus_module  # noqa: PLC0415

    return focus_module, fake


def test_first_30_minute_crossing_fires_and_persists(focus) -> None:
    focus_module, fake = focus
    session = _make_session(uid="l-t1", last_milestone_minutes=0)
    fake.caldav.entries[session.uid] = session
    now = session.start + timedelta(minutes=31)

    focus_module._maybe_fire_milestone(session, now)

    assert len(fake.notify.sends) == 1
    title, message, tags = fake.notify.sends[0]
    assert title == "Focus milestone"
    assert "30 min" in message
    assert tags == ["hourglass"]
    assert fake.caldav.updates == [(session.uid, TimeLogEntryUpdate(last_milestone_minutes=30))]
    assert session.last_milestone_minutes == 30


def test_already_persisted_crossing_does_not_refire(focus) -> None:
    focus_module, fake = focus
    session = _make_session(uid="l-t2", last_milestone_minutes=60)
    fake.caldav.entries[session.uid] = session

    focus_module._maybe_fire_milestone(session, session.start + timedelta(minutes=60))

    assert fake.notify.sends == []
    assert fake.caldav.updates == []


def test_back_fires_all_elapsed_crossings_after_refresh(focus) -> None:
    """Simulates the original bug: page refreshed at minute 91
    with ``last_milestone_minutes = 0``. The function must
    fire for 30, 60, and 90 in sequence and persist 90."""
    focus_module, fake = focus
    session = _make_session(uid="l-t3", last_milestone_minutes=0)
    fake.caldav.entries[session.uid] = session

    focus_module._maybe_fire_milestone(session, session.start + timedelta(minutes=91))

    assert len(fake.notify.sends) == 3
    assert "30 min" in fake.notify.sends[0][1]
    assert "60 min" in fake.notify.sends[1][1]
    assert "90 min" in fake.notify.sends[2][1]
    assert fake.caldav.updates == [(session.uid, TimeLogEntryUpdate(last_milestone_minutes=90))]
    assert session.last_milestone_minutes == 90


def test_in_memory_cache_suppresses_steady_state_put(focus) -> None:
    """A second tick within the same page load at the same minute
    value must not re-issue a PUT. ADR-028 optimisation."""
    focus_module, fake = focus
    session = _make_session(uid="l-t4", last_milestone_minutes=30)
    fake.caldav.entries[session.uid] = session
    cache_key = f"focus_notify_max_{session.uid}"
    focus_module.st.session_state[cache_key] = 30

    # Tick at minute 31: nothing has crossed since 30, no work.
    focus_module._maybe_fire_milestone(session, session.start + timedelta(minutes=31))
    assert fake.caldav.updates == []
    assert fake.notify.sends == []

    # Tick at minute 61: 60 just crossed, one PUT, one notify.
    focus_module._maybe_fire_milestone(session, session.start + timedelta(minutes=61))
    assert fake.caldav.updates == [(session.uid, TimeLogEntryUpdate(last_milestone_minutes=60))]
    assert len(fake.notify.sends) == 1
    assert "60 min" in fake.notify.sends[0][1]


def test_put_failure_another_device_won_skips_silently(focus) -> None:
    """412 race: this device's stale view of the session has
    ``last_milestone_minutes = 0``; PUT fails because the
    server's version is already at 30 (the other device won).
    A refetch returns the server version (30), the function
    sees the field has already moved past ``new_max``, and
    skips the notify."""
    focus_module, fake = focus
    stale = _make_session(uid="l-t5", last_milestone_minutes=0)
    fresh = _make_session(uid="l-t5", last_milestone_minutes=30)
    fake.caldav.entries[stale.uid] = fresh  # server view
    fake.caldav.put_mode = "fail"

    focus_module._maybe_fire_milestone(stale, stale.start + timedelta(minutes=31))

    assert fake.notify.sends == []
    assert fake.caldav.updates == [(stale.uid, TimeLogEntryUpdate(last_milestone_minutes=30))]
    assert fake.caldav.gets == [stale.uid]
    # Cache is updated from the refetched value so we don't
    # re-attempt on the next tick.
    cache_key = f"focus_notify_max_{stale.uid}"
    assert focus_module.st.session_state[cache_key] == 30


def test_put_failure_stale_field_skips_notify_allows_retry(focus) -> None:
    """Transient PUT failure with no concurrent writer: skip this
    tick's notify, allow the next tick to retry."""
    focus_module, fake = focus
    session = _make_session(uid="l-t6", last_milestone_minutes=0)
    fake.caldav.entries[session.uid] = session
    fake.caldav.put_mode = "fail"

    focus_module._maybe_fire_milestone(session, session.start + timedelta(minutes=31))

    assert fake.notify.sends == []
    assert fake.caldav.updates == [(session.uid, TimeLogEntryUpdate(last_milestone_minutes=30))]
    assert fake.caldav.gets == [session.uid]


def test_aligned_session_includes_task_title(focus) -> None:
    """When the session is aligned to a task, the notification
    body includes the task title so the user can disambiguate
    from other sessions."""
    focus_module, fake = focus

    class _TaskStub:
        title = "Write ADR-028"

    fake.caldav.get_task = lambda _uid: _TaskStub()  # type: ignore[attr-defined]
    session = _make_session(uid="l-t7", last_milestone_minutes=0, task_id="t-abc")
    fake.caldav.entries[session.uid] = session

    focus_module._maybe_fire_milestone(session, session.start + timedelta(minutes=31))

    title, message, _ = fake.notify.sends[0]
    assert "Write ADR-028" in message


def test_below_first_crossing_does_nothing(focus) -> None:
    """Sessions under 30 minutes old must not fire and must not PUT."""
    focus_module, fake = focus
    session = _make_session(uid="l-t8", last_milestone_minutes=0)
    fake.caldav.entries[session.uid] = session

    focus_module._maybe_fire_milestone(session, session.start + timedelta(minutes=15))

    assert fake.notify.sends == []
    assert fake.caldav.updates == []
