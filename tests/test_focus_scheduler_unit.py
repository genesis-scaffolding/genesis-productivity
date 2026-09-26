"""Unit tests for ``genesis.focus_scheduler``.

Drives the scheduler's tick logic directly without bringing up
the Streamlit runtime. Covers three slices:

- **Tick body** (auto-stop + milestone notifications). Lifted
  from ``tests/test_focus_milestone_unit.py`` plus the new
  auto-stop cases. Calls ``_tick_once(now=...)`` directly so
  tests finish in well under a second and do not depend on
  ``time.sleep``.
- **Scheduler lifecycle** (start / stop idempotency, daemon
  thread flag, error swallowing on tick body).
- **Runtime cache** (per-process dedup, ``stop_for_user``
  removes entries, ``stop_all`` drains).

ADR-029.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from genesis.core.facade import Facade
from genesis.entities import (
    TimeLogEntry,
    TimeLogEntryUpdate,
    TimeLogMode,
    TimeLogStatus,
)
from genesis.focus_scheduler.runtime import (
    _schedulers,
    ensure_scheduler_running,
    stop_all,
    stop_for_user,
)
from genesis.focus_scheduler.scheduler import FocusScheduler

# -----------------------------------------------------------------------------
# Fakes
# -----------------------------------------------------------------------------


class _FakeTask:
    def __init__(self, title: str) -> None:
        self.title = title


class _FakeCaldav:
    """Minimal CalDAV facade for scheduler tick tests."""

    def __init__(self) -> None:
        self.entries: dict[str, TimeLogEntry] = {}
        self.updates: list[tuple[str, TimeLogEntryUpdate]] = []
        self.gets: list[str] = []
        self.list_calls: int = 0
        # "ok" returns the entry on update; "fail" returns None.
        self.put_mode = "ok"
        # If set, ``list_time_log`` raises this on the next call.
        self.list_raises: Exception | None = None
        # Tasks keyed by UID; tests inject aligned-task fixtures here.
        self.tasks: dict[str, _FakeTask] = {}

    def list_time_log(self, *, active_only: bool = False):
        self.list_calls += 1
        if self.list_raises is not None:
            exc = self.list_raises
            self.list_raises = None
            raise exc
        return list(self.entries.values())

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

    def get_task(self, task_id: str):
        return self.tasks.get(task_id)


class _FakeNotify:
    def __init__(self) -> None:
        self.sends: list[tuple[str, str, list[str]]] = []

    def send(self, title: str, message: str, *, tags: list[str] | None = None) -> None:
        self.sends.append((title, message, list(tags or [])))


class _FakeFacade:
    """Minimal Facade for scheduler tick tests.

    The scheduler reads ``facade.configs.user``, ``facade.caldav``,
    ``facade.stop_session``, ``facade.notify``. Everything else
    is not exercised.
    """

    class _Configs:
        user = "alice"

    def __init__(self) -> None:
        self.caldav = _FakeCaldav()
        self.notify = _FakeNotify()
        self.configs = self._Configs()
        self.stop_calls: list[tuple[str, dict[str, Any]]] = []
        # When ``stop_returns`` is None, ``stop_session`` returns the entry
        # (race winner). When set to ``None`` explicitly via the ``stop_mode``
        # attribute, ``stop_session`` returns ``None`` (race loser).
        self.stop_mode = "ok"

    def stop_session(self, uid: str, **kwargs):
        self.stop_calls.append((uid, kwargs))
        if self.stop_mode == "fail":
            return None
        # ``stop_session`` also writes status=COMPLETED and
        # completed_at=completed_at in real life. Mirror that so
        # the auto-stop's effect on the entry is observable.
        entry = self.caldav.entries.get(uid)
        if entry is not None:
            entry.status = TimeLogStatus.COMPLETED
            entry.completed_at = kwargs.get("completed_at")
            if kwargs.get("actual_due") is not None:
                entry.due = kwargs["actual_due"]
        return entry


def _make_session(
    *,
    uid: str,
    start: datetime | None = None,
    due: datetime | None = None,
    mode: TimeLogMode = TimeLogMode.COUNTUP,
    status: TimeLogStatus = TimeLogStatus.IN_PROGRESS,
    task_id: str | None = None,
    last_milestone_minutes: int = 0,
) -> TimeLogEntry:
    return TimeLogEntry(
        uid=uid,
        label="Test session",
        start=start or datetime(2026, 9, 9, 21, 0, tzinfo=UTC),
        due=due,
        mode=mode,
        status=status,
        task_id=task_id,
        last_milestone_minutes=last_milestone_minutes,
    )


@pytest.fixture
def fake() -> _FakeFacade:
    """A fresh fake facade; cleared between tests.

    Annotated as :class:`_FakeFacade` (not :class:`Facade`) so
    pyright accepts attribute access on the fake's recorder
    fields (``entries``, ``sends``, ``put_mode``). Call sites
    that pass ``fake`` to :class:`FocusScheduler` carry a
    per-call ``# type: ignore[arg-type]`` because the structural
    mismatch is intentional — the fake implements only the
    surfaces the scheduler actually touches.
    """
    return _FakeFacade()


@pytest.fixture(autouse=True)
def _clear_runtime_cache() -> Iterator[None]:
    """Clear the runtime cache before and after every test.

    Several tests in this file construct schedulers directly;
    others go through ``ensure_scheduler_running``. The cache
    is process-global; without this fixture, an earlier test
    would leak a scheduler into a later one.
    """
    stop_all(timeout=1.0)
    _schedulers.clear()
    yield
    stop_all(timeout=1.0)
    _schedulers.clear()


# -----------------------------------------------------------------------------
# Auto-stop (countdown)
# -----------------------------------------------------------------------------


def test_tick_auto_stops_expired_countdown_and_fires_finished(fake: _FakeFacade) -> None:
    """A countdown whose ``due`` has passed must be auto-stopped
    and the finished notification must fire once."""
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    session = _make_session(
        uid="l-tick-auto-1",
        start=start,
        due=start + timedelta(minutes=25),
        mode=TimeLogMode.COUNTDOWN,
    )
    fake.caldav.entries[session.uid] = session
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    now = start + timedelta(minutes=26)
    scheduler._tick_once(now=now)

    assert len(fake.stop_calls) == 1
    uid, kwargs = fake.stop_calls[0]
    assert uid == session.uid
    assert kwargs["actual_due"] == session.due
    assert kwargs["completed_at"] == now
    assert len(fake.notify.sends) == 1
    title, message, tags = fake.notify.sends[0]
    assert title == "Focus session finished"
    assert tags == ["check"]


def test_tick_does_not_auto_stop_future_due_session(fake: _FakeFacade) -> None:
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    session = _make_session(
        uid="l-tick-auto-2",
        start=start,
        due=start + timedelta(minutes=25),
        mode=TimeLogMode.COUNTDOWN,
    )
    fake.caldav.entries[session.uid] = session
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    scheduler._tick_once(now=start + timedelta(minutes=10))

    assert fake.stop_calls == []
    assert fake.notify.sends == []


def test_tick_auto_stop_race_loss_does_not_fire_finished(fake: _FakeFacade) -> None:
    """If ``stop_session`` returns ``None`` (If-Match race loss),
    the finished notification must NOT fire — the race winner
    will fire it."""
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    session = _make_session(
        uid="l-tick-auto-3",
        start=start,
        due=start + timedelta(minutes=25),
        mode=TimeLogMode.COUNTDOWN,
    )
    fake.caldav.entries[session.uid] = session
    fake.stop_mode = "fail"
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    scheduler._tick_once(now=start + timedelta(minutes=26))

    assert fake.stop_calls and len(fake.stop_calls) == 1
    assert fake.notify.sends == []


def test_tick_auto_stop_aligned_session_includes_task_title(fake: _FakeFacade) -> None:
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    session = _make_session(
        uid="l-tick-auto-4",
        start=start,
        due=start + timedelta(minutes=25),
        mode=TimeLogMode.COUNTDOWN,
        task_id="task-xyz",
    )
    fake.caldav.entries[session.uid] = session
    fake.caldav.tasks["task-xyz"] = _FakeTask("Write ADR-029")
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    scheduler._tick_once(now=start + timedelta(minutes=26))

    title, message, _ = fake.notify.sends[0]
    assert "Write ADR-029" in message


def test_tick_auto_stop_countup_session_is_a_noop(fake: _FakeFacade) -> None:
    """A count-up session has no ``due``; it must never auto-stop."""
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    session = _make_session(uid="l-tick-auto-5", start=start, mode=TimeLogMode.COUNTUP)
    fake.caldav.entries[session.uid] = session
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    scheduler._tick_once(now=start + timedelta(minutes=10))

    assert fake.stop_calls == []
    assert fake.notify.sends == []


def test_tick_auto_stop_already_completed_session_is_a_noop(fake: _FakeFacade) -> None:
    """A session that some other device already stopped must not
    be re-stopped and must not fire a duplicate notification."""
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    session = _make_session(
        uid="l-tick-auto-6",
        start=start,
        due=start + timedelta(minutes=25),
        mode=TimeLogMode.COUNTDOWN,
        status=TimeLogStatus.COMPLETED,
    )
    fake.caldav.entries[session.uid] = session
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    scheduler._tick_once(now=start + timedelta(minutes=26))

    assert fake.stop_calls == []
    assert fake.notify.sends == []


# -----------------------------------------------------------------------------
# Milestone notifications (lifted from test_focus_milestone_unit.py)
# -----------------------------------------------------------------------------


def test_tick_fires_first_30_minute_crossing_and_persists(fake: _FakeFacade) -> None:
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    session = _make_session(uid="l-tick-m-1", start=start, last_milestone_minutes=0)
    fake.caldav.entries[session.uid] = session
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    scheduler._tick_once(now=start + timedelta(minutes=31))

    assert len(fake.notify.sends) == 1
    title, message, tags = fake.notify.sends[0]
    assert title == "Focus milestone"
    assert "30 min" in message
    assert tags == ["hourglass"]
    assert fake.caldav.updates == [(session.uid, TimeLogEntryUpdate(last_milestone_minutes=30))]
    assert session.last_milestone_minutes == 30


def test_tick_does_not_refire_already_persisted_crossing(fake: _FakeFacade) -> None:
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    session = _make_session(uid="l-tick-m-2", start=start, last_milestone_minutes=60)
    fake.caldav.entries[session.uid] = session
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    scheduler._tick_once(now=start + timedelta(minutes=60))

    assert fake.notify.sends == []
    assert fake.caldav.updates == []


def test_tick_back_fires_all_elapsed_crossings(fake: _FakeFacade) -> None:
    """A long-running session rendered for the first time at
    minute 91 with ``last_milestone_minutes = 0`` must fire for
    30, 60, and 90 in sequence and persist 90. Same regression
    that drove ADR-028."""
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    session = _make_session(uid="l-tick-m-3", start=start, last_milestone_minutes=0)
    fake.caldav.entries[session.uid] = session
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    scheduler._tick_once(now=start + timedelta(minutes=91))

    assert len(fake.notify.sends) == 3
    assert "30 min" in fake.notify.sends[0][1]
    assert "60 min" in fake.notify.sends[1][1]
    assert "90 min" in fake.notify.sends[2][1]
    assert fake.caldav.updates == [(session.uid, TimeLogEntryUpdate(last_milestone_minutes=90))]
    assert session.last_milestone_minutes == 90


def test_tick_in_memory_cache_suppresses_steady_state_put(fake: _FakeFacade) -> None:
    """A second tick within the same scheduler instance at the
    same minute value must not re-issue a PUT. The cache is the
    scheduler's replacement for the page's ``st.session_state``
    optimisation."""
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    session = _make_session(uid="l-tick-m-4", start=start, last_milestone_minutes=30)
    fake.caldav.entries[session.uid] = session
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    # First tick at minute 31: nothing has crossed since 30.
    scheduler._tick_once(now=start + timedelta(minutes=31))
    assert fake.caldav.updates == []
    assert fake.notify.sends == []

    # Second tick at minute 61: 60 just crossed.
    scheduler._tick_once(now=start + timedelta(minutes=61))
    assert fake.caldav.updates == [(session.uid, TimeLogEntryUpdate(last_milestone_minutes=60))]
    assert len(fake.notify.sends) == 1
    assert "60 min" in fake.notify.sends[0][1]

    # Third tick at minute 61 (same minute, same scheduler):
    # cache suppresses the PUT entirely.
    fake.caldav.updates.clear()
    fake.notify.sends.clear()
    scheduler._tick_once(now=start + timedelta(minutes=61, seconds=5))
    assert fake.caldav.updates == []
    assert fake.notify.sends == []


def test_tick_put_failure_another_device_won_skips_silently(fake: _FakeFacade) -> None:
    """412 race: this scheduler's stale view has
    ``last_milestone_minutes = 0``; PUT fails because the
    server view is already at 30 (the other device won). A
    refetch returns the server view; the function sees the
    field has already moved past ``new_max`` and skips."""
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    stale = _make_session(uid="l-tick-m-5", start=start, last_milestone_minutes=0)
    fresh = _make_session(uid="l-tick-m-5", start=start, last_milestone_minutes=30)
    # ``entries`` is what list_time_log returns (this device's
    # stale view). ``get_time_log_entry`` is wrapped below so
    # it still records the call in ``fake.caldav.gets`` but
    # returns the fresh server view, simulating a refetch
    # after the PUT race loss.
    fake.caldav.entries[stale.uid] = stale

    def _refetch_as_server(uid: str) -> TimeLogEntry | None:
        fake.caldav.gets.append(uid)
        return fresh

    fake.caldav.get_time_log_entry = _refetch_as_server  # type: ignore[method-assign]
    fake.caldav.put_mode = "fail"
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    scheduler._tick_once(now=start + timedelta(minutes=31))

    assert fake.notify.sends == []
    assert fake.caldav.updates == [(stale.uid, TimeLogEntryUpdate(last_milestone_minutes=30))]
    assert fake.caldav.gets == [stale.uid]


def test_tick_put_failure_stale_field_skips_notify_allows_retry(fake: _FakeFacade) -> None:
    """Transient PUT failure with no concurrent writer: skip
    this tick's notify, allow the next tick to retry."""
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    session = _make_session(uid="l-tick-m-6", start=start, last_milestone_minutes=0)
    fake.caldav.entries[session.uid] = session
    fake.caldav.put_mode = "fail"
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    scheduler._tick_once(now=start + timedelta(minutes=31))

    assert fake.notify.sends == []
    assert fake.caldav.updates == [(session.uid, TimeLogEntryUpdate(last_milestone_minutes=30))]
    assert fake.caldav.gets == [session.uid]


def test_tick_aligned_milestone_includes_task_title(fake: _FakeFacade) -> None:
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    session = _make_session(uid="l-tick-m-7", start=start, last_milestone_minutes=0, task_id="t-abc")
    fake.caldav.entries[session.uid] = session
    fake.caldav.tasks["t-abc"] = _FakeTask("Write ADR-029")
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    scheduler._tick_once(now=start + timedelta(minutes=31))

    title, message, _ = fake.notify.sends[0]
    assert "Write ADR-029" in message


def test_tick_below_first_crossing_does_nothing(fake: _FakeFacade) -> None:
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    session = _make_session(uid="l-tick-m-8", start=start, last_milestone_minutes=0)
    fake.caldav.entries[session.uid] = session
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    scheduler._tick_once(now=start + timedelta(minutes=15))

    assert fake.notify.sends == []
    assert fake.caldav.updates == []


# -----------------------------------------------------------------------------
# Tick-level error handling
# -----------------------------------------------------------------------------


def test_tick_swallows_list_time_log_exception(fake: _FakeFacade, caplog: pytest.LogCaptureFixture) -> None:
    """A CalDAV list failure must not crash the thread. The
    scheduler logs a warning and exits the tick cleanly."""
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]
    fake.caldav.list_raises = RuntimeError("network down")

    with caplog.at_level("WARNING"):
        scheduler._tick_once(now=datetime.now(UTC))

    assert fake.notify.sends == []
    assert fake.caldav.updates == []
    # The scheduler's tick_count still increments; errors are
    # observed by logging, not by counter drift.
    assert scheduler.tick_count == 1


def test_tick_swallows_get_time_log_entry_exception(fake: _FakeFacade) -> None:
    """A PUT race-loss refetch that itself fails must not
    crash the tick. The scheduler logs a warning and skips."""
    start = datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    session = _make_session(uid="l-tick-err", start=start, last_milestone_minutes=0)
    fake.caldav.entries[session.uid] = session
    fake.caldav.put_mode = "fail"

    original = fake.caldav.get_time_log_entry

    def _raising(_uid: str):
        raise RuntimeError("refetch failed")

    fake.caldav.get_time_log_entry = _raising  # type: ignore[method-assign]
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]

    # Should not raise. The refetch error path is exercised
    # when put_mode=fail (which triggers the refetch branch).
    scheduler._tick_once(now=start + timedelta(minutes=31))

    assert fake.notify.sends == []
    # Restore for cleanliness; not strictly needed.
    fake.caldav.get_time_log_entry = original  # type: ignore[method-assign]


# -----------------------------------------------------------------------------
# Lifecycle
# -----------------------------------------------------------------------------


def test_start_is_idempotent(fake: _FakeFacade) -> None:
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]
    scheduler.start()
    first_thread = scheduler._thread
    scheduler.start()
    assert scheduler._thread is first_thread
    scheduler.stop(timeout=2.0)


def test_stop_before_start_is_a_noop(fake: _FakeFacade) -> None:
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]
    scheduler.stop()  # must not raise


def test_stop_is_idempotent(fake: _FakeFacade) -> None:
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]
    scheduler.start()
    scheduler.stop(timeout=2.0)
    scheduler.stop()  # must not raise


def test_thread_is_daemon(fake: _FakeFacade) -> None:
    """A daemon thread cannot block process exit. ADR-029."""
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]
    scheduler.start()
    try:
        assert scheduler._thread is not None
        assert scheduler._thread.daemon is True
    finally:
        scheduler.stop(timeout=2.0)


def test_invalid_tick_seconds_rejected(fake: _FakeFacade) -> None:
    with pytest.raises(ValueError, match="tick_seconds must be positive"):
        FocusScheduler(facade=fake, tick_seconds=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="tick_seconds must be positive"):
        FocusScheduler(facade=fake, tick_seconds=-1.0)  # type: ignore[arg-type]


def test_is_running_reflects_thread_state(fake: _FakeFacade) -> None:
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]
    assert scheduler.is_running is False
    scheduler.start()
    assert scheduler.is_running is True
    scheduler.stop(timeout=2.0)
    assert scheduler.is_running is False


def test_tick_increments_tick_count_and_last_tick_at(fake: _FakeFacade) -> None:
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]
    assert scheduler.tick_count == 0
    assert scheduler.last_tick_at is None

    now = datetime(2026, 9, 9, 22, 0, tzinfo=UTC)
    scheduler._tick_once(now=now)

    assert scheduler.tick_count == 1
    assert scheduler.last_tick_at == now


def test_thread_exits_cleanly_on_stop(fake: _FakeFacade) -> None:
    """The daemon thread must exit within ``timeout`` after
    ``stop()`` is called."""
    scheduler = FocusScheduler(facade=fake, tick_seconds=10.0)  # type: ignore[arg-type]
    scheduler.start()
    thread = scheduler._thread
    assert thread is not None
    assert thread.is_alive()

    scheduler.stop(timeout=5.0)
    assert not thread.is_alive()


# -----------------------------------------------------------------------------
# Runtime cache
# -----------------------------------------------------------------------------


class _StubFacade:
    """Bare-bones facade stand-in for the cache tests.

    Avoids constructing a real ``Facade`` (which would try to
    start Radicale). The runtime cache only needs ``configs.user``
    to build the thread name; the rest of the scheduler never
    runs in these tests because we always ``stop()`` it before
    the first tick.
    """

    class _Configs:
        user = "alice"

    configs = _Configs()
    caldav = _FakeCaldav()
    notify = _FakeNotify()


def test_ensure_scheduler_running_returns_same_instance_for_same_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two callers for the same user must get the same scheduler
    instance (and therefore the same daemon thread)."""
    from genesis.focus_scheduler import runtime

    constructed: list[object] = []

    def _fake_facade_ctor(yaml_path: object = None, **kwargs: object) -> object:
        f = _StubFacade()
        constructed.append(f)
        return f

    monkeypatch.setattr(runtime, "Facade", _fake_facade_ctor)

    a = ensure_scheduler_running("alice")
    b = ensure_scheduler_running("alice")
    assert a is b
    assert len(constructed) == 1


def test_ensure_scheduler_running_uses_separate_instances_per_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Different users get different schedulers."""
    from genesis.focus_scheduler import runtime

    def _fake_facade_ctor(yaml_path: object = None, **kwargs: object) -> object:
        f = _StubFacade()
        # Vary the user so the runtime cache keys differ.
        f._Configs.user = "bob"  # type: ignore[attr-defined]
        return f

    monkeypatch.setattr(runtime, "Facade", _fake_facade_ctor)

    alice = ensure_scheduler_running("alice-uniq-2")
    bob = ensure_scheduler_running("bob-uniq-2")
    assert alice is not bob
    assert "alice-uniq-2" in _schedulers
    assert "bob-uniq-2" in _schedulers


def test_stop_for_user_removes_cache_entry() -> None:
    """Insert a scheduler into the cache directly, then call
    ``stop_for_user`` and confirm the entry is gone and the
    thread has exited."""
    fake = cast(Facade, _FakeFacade())
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]
    scheduler.start()
    _schedulers["alice-cache-1"] = scheduler

    stop_for_user("alice-cache-1", timeout=1.0)

    assert "alice-cache-1" not in _schedulers
    assert scheduler.is_running is False


def test_stop_for_user_missing_user_is_noop() -> None:
    stop_for_user("ghost-user-never-ran", timeout=1.0)


def test_stop_all_drains_every_cached_scheduler() -> None:
    fake = cast(Facade, _FakeFacade())
    a = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]
    a.start()
    _schedulers["drain-a"] = a
    b = FocusScheduler(facade=fake, tick_seconds=0.01)  # type: ignore[arg-type]
    b.start()
    _schedulers["drain-b"] = b

    stop_all(timeout=2.0)

    assert _schedulers == {}
    assert a.is_running is False
    assert b.is_running is False


# -----------------------------------------------------------------------------
# Real-thread smoke
# -----------------------------------------------------------------------------


def test_real_thread_ticks_at_least_once(fake: _FakeFacade) -> None:
    """End-to-end smoke: start the daemon thread, wait a moment,
    confirm at least one tick fired, then stop. Verifies the
    ``threading.Thread`` plumbing, the ``threading.Event``
    shutdown signal, and the daemon-flag interaction."""
    scheduler = FocusScheduler(facade=fake, tick_seconds=0.05)  # type: ignore[arg-type]
    scheduler.start()
    try:
        deadline = time.monotonic() + 2.0
        while scheduler.tick_count < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert scheduler.tick_count >= 1, "scheduler thread never ticked within 2s"
    finally:
        scheduler.stop(timeout=2.0)
