"""Unit tests for the category module.

Covers the enum, the pure inference function, the facade's history
lookup, and the migration CLI's behavior on a mock caldav.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from genesis.category import migration as migration_mod
from genesis.category.enums import TimeLogCategory
from genesis.category.facade import CategoryFacade
from genesis.category.inference import infer_category
from genesis.entities import (
    TimeLogEntry,
    TimeLogEntryUpdate,
    TimeLogMode,
    TimeLogStatus,
)


def _entry(
    *,
    uid: str,
    label: str,
    category: TimeLogCategory,
    task_id: str | None = None,
    start: datetime | None = None,
) -> TimeLogEntry:
    return TimeLogEntry(
        uid=uid,
        label=label,
        start=start or datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
        due=datetime(2026, 9, 8, 10, 30, tzinfo=UTC),
        completed_at=datetime(2026, 9, 8, 10, 30, tzinfo=UTC),
        status=TimeLogStatus.COMPLETED,
        mode=TimeLogMode.COUNTDOWN,
        task_id=task_id,
        category=category,
    )


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------


def test_time_log_category_values_are_stable() -> None:
    """Wire shape depends on these exact strings."""
    assert TimeLogCategory.WORK.value == "work"
    assert TimeLogCategory.PLAY.value == "play"
    assert TimeLogCategory.WORKOUT.value == "workout"
    assert TimeLogCategory.PERSONAL.value == "personal"
    assert TimeLogCategory.OTHER.value == "other"


def test_time_log_category_iterates_in_definition_order() -> None:
    """The stats module relies on enum iteration order for KPI keys."""
    assert [c.value for c in TimeLogCategory] == [
        "work",
        "play",
        "workout",
        "personal",
        "other",
    ]


# ---------------------------------------------------------------------------
# Pure inference
# ---------------------------------------------------------------------------


def test_infer_task_aligned_is_work() -> None:
    """Task-aligned sessions default to Work regardless of label."""
    assert infer_category("Cooking", "t-abc", []) == TimeLogCategory.WORK
    assert infer_category("anything", "t-abc", []) == TimeLogCategory.WORK


def test_infer_history_takes_precedence_over_keywords() -> None:
    """If a prior session with the same label exists, use its category."""
    history = [("Cooking", TimeLogCategory.WORKOUT)]
    # Without history the label would match "cook" -> PERSONAL.
    assert infer_category("Cooking", None, history) == TimeLogCategory.WORKOUT


def test_infer_history_uses_most_recent_entry() -> None:
    """Most recent first wins; older entries are ignored."""
    history = [
        ("Cooking", TimeLogCategory.PERSONAL),
        ("Cooking", TimeLogCategory.WORK),
    ]
    assert infer_category("Cooking", None, history) == TimeLogCategory.PERSONAL


def test_infer_history_matches_only_same_label() -> None:
    history = [("Walking", TimeLogCategory.WORKOUT)]
    # "Foo" has no keyword match and no history match -> None.
    assert infer_category("Foo", None, history) is None


def test_infer_workout_keyword() -> None:
    for label in ("Gym session", "morning run", "YOGA", "weight lifting"):
        assert infer_category(label, None, []) == TimeLogCategory.WORKOUT, label


def test_infer_play_keyword() -> None:
    for label in ("Zelda", "Netflix", "PS5 gaming", "Reading a book"):
        assert infer_category(label, None, []) == TimeLogCategory.PLAY, label


def test_infer_personal_keyword() -> None:
    for label in ("Cooking dinner", "Laundry", "Family dinner"):
        assert infer_category(label, None, []) == TimeLogCategory.PERSONAL, label


def test_infer_unmatched_label_returns_none() -> None:
    assert infer_category("Misc", None, []) is None


def test_infer_empty_label_returns_none() -> None:
    assert infer_category("", None, []) is None


def test_infer_keywords_are_case_insensitive() -> None:
    assert infer_category("GYM", None, []) == TimeLogCategory.WORKOUT
    assert infer_category("cooking", None, []) == TimeLogCategory.PERSONAL


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------


class _FakeCalDAV:
    def __init__(self, entries: list[TimeLogEntry]) -> None:
        self._entries = entries

    def list_time_log(self) -> list[TimeLogEntry]:
        return list(self._entries)


def test_facade_infer_uses_history_from_caldav() -> None:
    fake = _FakeCalDAV(
        [
            _entry(uid="newer", label="Cooking", category=TimeLogCategory.PERSONAL),
            _entry(uid="old", label="Cooking", category=TimeLogCategory.WORKOUT),
        ]
    )
    f = CategoryFacade(fake)  # type: ignore[arg-type]
    assert f.infer("Cooking", None) == TimeLogCategory.PERSONAL


def test_facade_infer_falls_back_to_keywords_when_no_history() -> None:
    fake = _FakeCalDAV([_entry(uid="other", label="Lunch", category=TimeLogCategory.PERSONAL)])
    f = CategoryFacade(fake)  # type: ignore[arg-type]
    # No prior "Gym" entry, so keyword match -> Workout.
    assert f.infer("Gym", None) == TimeLogCategory.WORKOUT


def test_facade_infer_returns_none_when_no_signal() -> None:
    fake = _FakeCalDAV([])
    f = CategoryFacade(fake)  # type: ignore[arg-type]
    assert f.infer("Misc thing", None) is None


# ---------------------------------------------------------------------------
# Migration CLI
# ---------------------------------------------------------------------------


class _FakeEntry:
    def __init__(self, uid: str, label: str, category: TimeLogCategory, task_id: str | None = None) -> None:
        self.uid = uid
        self.label = label
        self.category = category
        self.task_id = task_id


class _FakeCalDAVMigration:
    def __init__(self, entries: list[_FakeEntry]) -> None:
        self._entries = list(entries)
        self.updates: list[tuple[str, TimeLogEntryUpdate]] = []

    def list_time_log(self, *, active_only: bool = False) -> list[Any]:
        return list(self._entries)

    def update_time_log_entry(self, uid: str, data: TimeLogEntryUpdate) -> Any:
        self.updates.append((uid, data))
        for e in self._entries:
            if e.uid == uid:
                if data.category is not None:
                    e.category = data.category
                return e
        return None


class _FakeFacade:
    def __init__(self, caldav: _FakeCalDAVMigration) -> None:
        self.caldav = caldav

    def shutdown(self) -> None:
        pass


def test_migration_updates_only_other_entries(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    entries = [
        _FakeEntry("a", "Gym session", TimeLogCategory.OTHER),
        _FakeEntry("b", "Lunch", TimeLogCategory.PERSONAL),  # already set
        _FakeEntry("c", "Zelda", TimeLogCategory.OTHER),
        _FakeEntry("d", "Misc", TimeLogCategory.OTHER),  # no signal -> skipped
    ]
    fake_caldav = _FakeCalDAVMigration(entries)
    fake_facade = _FakeFacade(fake_caldav)  # type: ignore[arg-type]

    monkeypatch.setattr(migration_mod, "resolve_config_path", lambda _user: "/dev/null")
    monkeypatch.setattr(migration_mod, "Facade", lambda *_args, **_kwargs: fake_facade)

    rc = migration_mod.run(dry_run=False)
    assert rc == 0
    uids_updated = [u for u, _ in fake_caldav.updates]
    assert uids_updated == ["a", "c"]
    out = capsys.readouterr().out
    assert "Changed 2 entries" in out
    assert "skipped 1" in out


def test_migration_dry_run_writes_nothing(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    entries = [
        _FakeEntry("a", "Gym session", TimeLogCategory.OTHER),
    ]
    fake_caldav = _FakeCalDAVMigration(entries)
    fake_facade = _FakeFacade(fake_caldav)  # type: ignore[arg-type]

    monkeypatch.setattr(migration_mod, "resolve_config_path", lambda _user: "/dev/null")
    monkeypatch.setattr(migration_mod, "Facade", lambda *_args, **_kwargs: fake_facade)

    rc = migration_mod.run(dry_run=True)
    assert rc == 0
    assert fake_caldav.updates == []
    out = capsys.readouterr().out
    assert "Would change 1 entries" in out
    assert entries[0].category == TimeLogCategory.OTHER


def test_migration_dry_run_reports_skipped(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    entries = [
        _FakeEntry("a", "Misc", TimeLogCategory.OTHER),  # no signal
    ]
    fake_caldav = _FakeCalDAVMigration(entries)
    fake_facade = _FakeFacade(fake_caldav)  # type: ignore[arg-type]

    monkeypatch.setattr(migration_mod, "resolve_config_path", lambda _user: "/dev/null")
    monkeypatch.setattr(migration_mod, "Facade", lambda *_args, **_kwargs: fake_facade)

    rc = migration_mod.run(dry_run=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Would change 0 entries" in out
    assert "skipped 1" in out


def test_migration_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = [
        _FakeEntry("a", "Gym session", TimeLogCategory.WORKOUT),  # already filled
    ]
    fake_caldav = _FakeCalDAVMigration(entries)
    fake_facade = _FakeFacade(fake_caldav)  # type: ignore[arg-type]

    monkeypatch.setattr(migration_mod, "resolve_config_path", lambda _user: "/dev/null")
    monkeypatch.setattr(migration_mod, "Facade", lambda *_args, **_kwargs: fake_facade)

    rc = migration_mod.run(dry_run=False)
    assert rc == 0
    assert fake_caldav.updates == []
