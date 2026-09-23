"""Unit tests for the backfill_xp script (ADR-026 migration).

Fast, in-memory tests covering the inference rules, idempotency
key derivation, date-window filtering, and dry-run vs apply
behaviour. The script does its own CalDAV reads/writes via the
``Facade`` it constructs, so these tests patch the
``resolve_config_path`` indirection to return a temp configs.yaml
that points at an in-memory ``MockCalDAVFacade``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import scripts.backfill_xp as backfill
from genesis.category.enums import TimeLogCategory
from genesis.configs import GameConfig
from genesis.core.facade import Facade
from genesis.entities import (
    TaskCreate,
    TaskStatus,
    TaskUpdate,
    TimeLogEntryCreate,
    TimeLogMode,
    TimeLogStatus,
    XpEventKind,
)
from tests.mock_caldav import MockCalDAVFacade

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a configs.yaml at tmp_path/<user>/configs.yaml and make
    ``resolve_config_path`` return it for the default user."""
    user_dir = tmp_path / "default"
    user_dir.mkdir()
    p = user_dir / "configs.yaml"
    p.write_text("dav:\n  password: test-pw\n")
    monkeypatch.setattr(
        "genesis.users.store._default_store",
        # Avoid touching the real users table on Facade boot.
        type("_S", (), {"verify_credentials": staticmethod(lambda *a, **k: True)})(),
    )
    return p


def _build_facade(config_path: Path, monkeypatch: pytest.MonkeyPatch) -> Facade:
    """Build a real ``Facade`` whose caldav sub-facade uses the mock.

    The ``Facade`` lazy-imports ``CalDAVFacade`` from
    ``genesis.caldav.facade``, so we patch it at the source. Same
    technique the existing tests use for ``JournalFacade``.
    """
    mock_caldav = MockCalDAVFacade()
    monkeypatch.setattr(
        "genesis.caldav.facade.CalDAVFacade",
        lambda *a, **kw: mock_caldav,
    )
    facade = Facade(config_path, ensure_server=False)
    # Stash the mock for test access.
    facade._test_mock_caldav = mock_caldav  # type: ignore[attr-defined]
    return facade


def _seed_completed_task(mock: MockCalDAVFacade, title: str, days_ago: int = 1) -> str:
    completed_at = datetime.now(UTC) - timedelta(days=days_ago)
    t = mock.create_task(TaskCreate(title=title))
    mock.update_task(t.uid, TaskUpdate(status=TaskStatus.COMPLETED))
    # Manually stamp completed_at (the mock doesn't have a setter;
    # but the update_task helper sets it to now in MockCalDAV).
    # We adjust the in-memory record to reflect the desired window.
    mock._tasks[t.uid].completed_at = completed_at  # type: ignore[attr-defined]
    return t.uid


def _seed_completed_session(
    mock: MockCalDAVFacade,
    label: str,
    category: TimeLogCategory,
    days_ago: int = 1,
    duration_minutes: int = 50,
) -> str:
    completed_at = datetime.now(UTC) - timedelta(days=days_ago)
    start = completed_at - timedelta(minutes=duration_minutes)
    entry = mock.create_time_log_entry(
        TimeLogEntryCreate(
            label=label,
            mode=TimeLogMode.COUNTDOWN,
            start=start,
            due=completed_at,
            completed_at=completed_at,
            category=category,
            status=TimeLogStatus.COMPLETED,
        )
    )
    return entry.uid


def _seed_open_task(mock: MockCalDAVFacade, title: str) -> str:
    t = mock.create_task(TaskCreate(title=title))
    return t.uid


def _seed_open_session(mock: MockCalDAVFacade, label: str, category: TimeLogCategory) -> str:
    entry = mock.create_time_log_entry(
        TimeLogEntryCreate(
            label=label,
            mode=TimeLogMode.COUNTUP,
            start=datetime.now(UTC),
            category=category,
        )
    )
    return entry.uid


# ---------------------------------------------------------------------------
# Inference (mirrors GameFacade._resolve; ADR-026 reference impl)
# ---------------------------------------------------------------------------


class TestResolveTask:
    def test_default_to_discipline(self) -> None:
        from genesis.entities import Task

        task = Task(
            uid="t-x",
            title="something vague",
            description=None,
            status=TaskStatus.COMPLETED,
        )
        config = GameConfig()
        stat_id, delta, kind = backfill._resolve_task(task, config)
        assert stat_id == "discipline"
        assert delta == 5
        assert kind == XpEventKind.GAIN

    def test_knowledge_keyword_matches(self) -> None:
        from genesis.entities import Task

        task = Task(
            uid="t-x",
            title="read atomic habits chapter 3",
            description=None,
            status=TaskStatus.COMPLETED,
        )
        config = GameConfig()
        stat_id, _, _ = backfill._resolve_task(task, config)
        assert stat_id == "knowledge"

    def test_vitality_keyword_matches(self) -> None:
        from genesis.entities import Task

        task = Task(
            uid="t-x",
            title="gym push day",
            description=None,
            status=TaskStatus.COMPLETED,
        )
        config = GameConfig()
        stat_id, _, _ = backfill._resolve_task(task, config)
        assert stat_id == "vitality"


class TestResolveSession:
    def test_work_category_uses_tier1(self) -> None:
        from genesis.category.enums import TimeLogCategory
        from genesis.entities import TimeLogEntry

        completed_at = datetime.now(UTC)
        session = TimeLogEntry(
            uid="l-x",
            label="deep work",
            start=completed_at - timedelta(minutes=50),
            due=completed_at,
            completed_at=completed_at,
            status=TimeLogStatus.COMPLETED,
            mode=TimeLogMode.COUNTDOWN,
            category=TimeLogCategory.WORK,
        )
        config = GameConfig()
        result = backfill._resolve_session(session, config)
        assert result is not None
        stat_id, delta, kind = result
        assert stat_id == "discipline"
        assert delta == 2  # floor(50 / 25)
        assert kind == XpEventKind.GAIN

    def test_play_category_is_loss(self) -> None:
        from genesis.category.enums import TimeLogCategory
        from genesis.entities import TimeLogEntry

        completed_at = datetime.now(UTC)
        session = TimeLogEntry(
            uid="l-x",
            label="gaming",
            start=completed_at - timedelta(minutes=180),
            due=completed_at,
            completed_at=completed_at,
            status=TimeLogStatus.COMPLETED,
            mode=TimeLogMode.COUNTDOWN,
            category=TimeLogCategory.PLAY,
        )
        config = GameConfig()
        result = backfill._resolve_session(session, config)
        assert result is not None
        stat_id, delta, kind = result
        assert stat_id == "discipline"
        assert delta == -1  # -floor(180 / 120)
        assert kind == XpEventKind.LOSS

    def test_personal_no_keyword_returns_none(self) -> None:
        from genesis.category.enums import TimeLogCategory
        from genesis.entities import TimeLogEntry

        completed_at = datetime.now(UTC)
        session = TimeLogEntry(
            uid="l-x",
            label="zzz qqq",
            start=completed_at - timedelta(minutes=30),
            due=completed_at,
            completed_at=completed_at,
            status=TimeLogStatus.COMPLETED,
            mode=TimeLogMode.COUNTDOWN,
            category=TimeLogCategory.PERSONAL,
        )
        config = GameConfig()
        result = backfill._resolve_session(session, config)
        assert result is None

    def test_session_without_completed_at_returns_none(self) -> None:
        from genesis.category.enums import TimeLogCategory
        from genesis.entities import TimeLogEntry

        session = TimeLogEntry(
            uid="l-x",
            label="work",
            start=datetime.now(UTC),
            status=TimeLogStatus.IN_PROGRESS,
            mode=TimeLogMode.COUNTUP,
            category=TimeLogCategory.WORK,
        )
        config = GameConfig()
        result = backfill._resolve_session(session, config)
        assert result is None


# ---------------------------------------------------------------------------
# Date-window filter
# ---------------------------------------------------------------------------


class TestInWindow:
    def test_inclusive_lower(self) -> None:
        when = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        lower = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        upper = datetime(2026, 1, 20, tzinfo=UTC)
        assert backfill._in_window(when, lower, upper) is True

    def test_exclusive_upper(self) -> None:
        when = datetime(2026, 1, 20, 0, 0, tzinfo=UTC)
        lower = datetime(2026, 1, 15, tzinfo=UTC)
        upper = datetime(2026, 1, 20, tzinfo=UTC)
        assert backfill._in_window(when, lower, upper) is False

    def test_no_bounds(self) -> None:
        when = datetime.now(UTC)
        assert backfill._in_window(when, None, None) is True

    def test_none_when(self) -> None:
        assert backfill._in_window(None, None, None) is False


# ---------------------------------------------------------------------------
# Idempotency key derivation
# ---------------------------------------------------------------------------


class TestIdempotencyKeys:
    def test_task_key(self) -> None:
        assert "task:t-abc123:completed" == "task:t-abc123:completed"

    def test_session_key(self) -> None:
        assert "session:l-xyz789:stopped" == "session:l-xyz789:stopped"


# ---------------------------------------------------------------------------
# Full-run (dry-run + apply) via Facade + MockCalDAVFacade
# ---------------------------------------------------------------------------


class TestFullRun:
    """Drive the script end-to-end against an in-memory backend."""

    def _make_facade(self, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> Facade:
        return _build_facade(config_path, monkeypatch)

    def test_dry_run_writes_nothing(self, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = self._make_facade(config_path, monkeypatch)
        mock = facade._test_mock_caldav  # type: ignore[attr-defined]
        _seed_completed_task(mock, "finish paperwork")
        _seed_completed_session(mock, "deep work", TimeLogCategory.WORK)  # TimeLogCategory.WORK = 0

        rc = backfill.main(
            [
                "--config-path",
                str(config_path),
                "--dry-run",
                "--json",
            ]
        )
        assert rc == 0
        assert len(mock._xp_events) == 0  # nothing written

    def test_apply_writes_events(self, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = self._make_facade(config_path, monkeypatch)
        mock = facade._test_mock_caldav  # type: ignore[attr-defined]
        task_uid = _seed_completed_task(mock, "finish paperwork")
        session_uid = _seed_completed_session(mock, "deep work", TimeLogCategory.WORK)

        rc = backfill.main(
            [
                "--config-path",
                str(config_path),
                "--json",
            ]
        )
        assert rc == 0
        # Two events written.
        assert len(mock._xp_events) == 2
        task_event = next(e for e in mock._xp_events.values() if e.source_task_id == task_uid)
        session_event = next(e for e in mock._xp_events.values() if e.source_session_id == session_uid)
        assert task_event.stat_id == "discipline"
        assert task_event.delta == 5
        assert session_event.stat_id == "discipline"
        assert session_event.delta == 2

    def test_apply_is_idempotent(self, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = self._make_facade(config_path, monkeypatch)
        mock = facade._test_mock_caldav  # type: ignore[attr-defined]
        _seed_completed_task(mock, "finish paperwork")
        _seed_completed_session(mock, "deep work", TimeLogCategory.WORK)

        # First run writes.
        backfill.main(["--config-path", str(config_path)])
        first_count = len(mock._xp_events)
        assert first_count == 2

        # Second run: every candidate is skipped.
        out_lines: list[str] = []
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            backfill.main(["--config-path", str(config_path), "--json"])
            out_lines.append(buf.getvalue())
        payload = json.loads(buf.getvalue())
        assert payload["tasks"]["would_create"] == 0
        assert payload["tasks"]["skipped"] == 1
        assert payload["sessions"]["would_create"] == 0
        assert payload["sessions"]["skipped"] == 1
        assert len(mock._xp_events) == first_count

    def test_window_filters_by_completed_at(self, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = self._make_facade(config_path, monkeypatch)
        mock = facade._test_mock_caldav  # type: ignore[attr-defined]
        old = _seed_completed_task(mock, "old", days_ago=30)
        recent = _seed_completed_task(mock, "recent", days_ago=1)

        rc = backfill.main(
            [
                "--config-path",
                str(config_path),
                "--from",
                (datetime.now(UTC) - timedelta(days=7)).date().isoformat(),
                "--json",
            ]
        )
        assert rc == 0

        task_uids = [e.source_task_id for e in mock._xp_events.values() if e.source_task_id]
        assert recent in task_uids
        assert old not in task_uids

    def test_open_tasks_and_sessions_are_skipped(self, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = self._make_facade(config_path, monkeypatch)
        mock = facade._test_mock_caldav  # type: ignore[attr-defined]
        _seed_completed_task(mock, "done")
        _seed_open_task(mock, "open")
        _seed_completed_session(mock, "completed", TimeLogCategory.WORK)
        _seed_open_session(mock, "in-progress", TimeLogCategory.WORK)

        rc = backfill.main(
            [
                "--config-path",
                str(config_path),
                "--json",
            ]
        )
        assert rc == 0

        # Only the completed task + completed session produce events.
        task_events = [e for e in mock._xp_events.values() if e.source_task_id]
        session_events = [e for e in mock._xp_events.values() if e.source_session_id]
        assert len(task_events) == 1
        assert len(session_events) == 1

    def test_tasks_only_skips_sessions(self, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = self._make_facade(config_path, monkeypatch)
        mock = facade._test_mock_caldav  # type: ignore[attr-defined]
        _seed_completed_task(mock, "done")
        _seed_completed_session(mock, "deep work", TimeLogCategory.WORK)

        rc = backfill.main(
            [
                "--config-path",
                str(config_path),
                "--tasks-only",
                "--json",
            ]
        )
        assert rc == 0

        assert len(mock._xp_events) == 1
        assert any(e.source_task_id for e in mock._xp_events.values())

    def test_refuses_when_game_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        user_dir = tmp_path / "default"
        user_dir.mkdir()
        config_path = user_dir / "configs.yaml"
        config_path.write_text("game:\n  enabled: false\n")
        monkeypatch.setattr(
            "genesis.users.store._default_store",
            type("_S", (), {"verify_credentials": staticmethod(lambda *a, **k: True)})(),
        )

        rc = backfill.main(
            [
                "--config-path",
                str(config_path),
                "--dry-run",
            ]
        )
        assert rc == 3  # game-disabled exit code

    def test_unclassifiable_session_is_counted_failed(
        self, config_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        facade = self._make_facade(config_path, monkeypatch)
        mock = facade._test_mock_caldav  # type: ignore[attr-defined]
        # Seed a PERSONAL session with no keywords.
        _seed_completed_session(mock, "zzz qqq xxx", TimeLogCategory.PERSONAL)  # PERSONAL = 3

        rc = backfill.main(
            [
                "--config-path",
                str(config_path),
                "--json",
            ]
        )
        assert rc == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["sessions"]["found"] == 1
        assert payload["sessions"]["failed"] == 1
        assert "zzz qqq xxx" in payload["skipped_session_labels"]

    def test_play_session_writes_negative_event(self, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        facade = self._make_facade(config_path, monkeypatch)
        mock = facade._test_mock_caldav  # type: ignore[attr-defined]
        # PLAY = 4. 240 minutes of play → -2 discipline.
        _seed_completed_session(mock, "gaming", TimeLogCategory.PLAY, duration_minutes=240)

        rc = backfill.main(["--config-path", str(config_path)])
        assert rc == 0
        events = list(mock._xp_events.values())
        assert len(events) == 1
        assert events[0].delta == -2
        assert events[0].kind == XpEventKind.LOSS


# ---------------------------------------------------------------------------
# CLI argument validation
# ---------------------------------------------------------------------------


class TestCLIValidation:
    def test_mutually_exclusive_tasks_sessions(self) -> None:
        rc = backfill.main(["--tasks-only", "--sessions-only"])
        assert rc == 2

    def test_invalid_date(self) -> None:
        rc = backfill.main(["--from", "not-a-date"])
        assert rc == 2

    def test_from_after_to(self) -> None:
        rc = backfill.main(["--from", "2026-12-01", "--to", "2026-01-01"])
        assert rc == 2
