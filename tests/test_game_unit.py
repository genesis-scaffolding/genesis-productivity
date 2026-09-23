"""Unit tests for the game layer (ADR-026).

Pure-Python tests for the XP math, classifier, source objects, and
game facade orchestration. No real CalDAV; uses MockCalDAVFacade
for the few facade tests that need persistence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from genesis.caldav.facade import CalDAVFacade
from genesis.category.enums import TimeLogCategory
from genesis.configs import GameConfig, StatConfig
from genesis.entities import (
    TaskCreate,
    TaskStatus,
    TimeLogEntryCreate,
    TimeLogMode,
    XpEventKind,
)
from genesis.game import classify, labels, xp
from genesis.game.facade import (
    GameClassificationError,
    GameDisabled,
    GameFacade,
)
from genesis.game.source import ReasonSource, SessionSource, TaskSource
from tests.mock_caldav import MockCalDAVFacade

# ---------------------------------------------------------------------------
# XP curve
# ---------------------------------------------------------------------------


class TestXpCurve:
    def test_curve_monotonic(self) -> None:
        """Each level requires strictly more XP than the previous."""
        for prev, curr in zip(xp.XP_CURVE[: len(xp.XP_CURVE) - 1], xp.XP_CURVE[1:], strict=True):
            assert curr > prev

    def test_level_for_xp_floor(self) -> None:
        assert xp.level_for_xp(0) == 1
        assert xp.level_for_xp(29) == 1
        assert xp.level_for_xp(30) == 2
        assert xp.level_for_xp(79) == 2
        assert xp.level_for_xp(80) == 3
        assert xp.level_for_xp(1550) == 10
        assert xp.level_for_xp(9999) == xp.MAX_LEVEL

    def test_level_for_xp_negative(self) -> None:
        """Negative XP clamps to level 1."""
        assert xp.level_for_xp(-50) == 1

    def test_xp_to_next_level(self) -> None:
        """Returns the gap to the next level threshold."""
        # 30 XP is the L2 threshold, so 25 XP needs 5 more.
        assert xp.xp_to_next_level(1, 25) == 5
        # At max level returns None.
        assert xp.xp_to_next_level(xp.MAX_LEVEL, 9999) is None

    def test_session_xp_formula(self) -> None:
        """Per-event XP formulas match the ADR-026 numbers."""
        # WORK / WORKOUT / personal / other: floor(duration / 25)
        assert xp.delta_from_session_duration(0, TimeLogCategory.WORK) == 0
        assert xp.delta_from_session_duration(24, TimeLogCategory.WORK) == 0
        assert xp.delta_from_session_duration(25, TimeLogCategory.WORK) == 1
        assert xp.delta_from_session_duration(50, TimeLogCategory.WORK) == 2
        assert xp.delta_from_session_duration(100, TimeLogCategory.WORK) == 4
        assert xp.delta_from_session_duration(150, TimeLogCategory.WORK) == 6

    def test_play_loss_formula(self) -> None:
        """PLAY sessions deduct floor(duration / 120)."""
        assert xp.delta_from_session_duration(0, TimeLogCategory.PLAY) == 0
        assert xp.delta_from_session_duration(119, TimeLogCategory.PLAY) == 0
        assert xp.delta_from_session_duration(120, TimeLogCategory.PLAY) == -1
        assert xp.delta_from_session_duration(240, TimeLogCategory.PLAY) == -2
        assert xp.delta_from_session_duration(360, TimeLogCategory.PLAY) == -3

    def test_stat_from_session_category(self) -> None:
        """Tier-1 mapping covers WORK, WORKOUT, PLAY; rest is None."""
        assert xp.stat_from_session_category(TimeLogCategory.WORK) == "discipline"
        assert xp.stat_from_session_category(TimeLogCategory.WORKOUT) == "vitality"
        assert xp.stat_from_session_category(TimeLogCategory.PLAY) == "discipline"
        assert xp.stat_from_session_category(TimeLogCategory.PERSONAL) is None
        assert xp.stat_from_session_category(TimeLogCategory.OTHER) is None


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class TestClassifier:
    def test_knowledge_keywords(self) -> None:
        assert (
            classify.infer_stat_from_text("read atomic habits chapter 3", default_stat_id="discipline") == "knowledge"
        )
        assert classify.infer_stat_from_text("write ADR-027 spec", default_stat_id="discipline") == "knowledge"

    def test_vitality_keywords(self) -> None:
        assert classify.infer_stat_from_text("gym push day", default_stat_id="discipline") == "vitality"
        assert classify.infer_stat_from_text("morning workout at the gym", default_stat_id="discipline") == "vitality"

    def test_discipline_keywords(self) -> None:
        assert (
            classify.infer_stat_from_text("weekly review of project alpha", default_stat_id="discipline")
            == "discipline"
        )
        assert classify.infer_stat_from_text("finish the paperwork today", default_stat_id="discipline") == "discipline"

    def test_charisma_keywords(self) -> None:
        assert classify.infer_stat_from_text("lunch with dad", default_stat_id="discipline") == "charisma"
        assert (
            classify.infer_stat_from_text("call mom for anniversary catch up", default_stat_id="discipline")
            == "charisma"
        )

    def test_courage_phrase_needs_both_words(self) -> None:
        """The phrase 'difficult conversation' needs both tokens."""
        # Single 'difficult' alone should NOT trigger courage.
        assert classify.infer_stat_from_text("this is a difficult task", default_stat_id="discipline") == "discipline"
        # Both words present should.
        # (May also trigger charisma via 'conversation'; check it
        # picks the right one given the design note's tie-break.)
        result = classify.infer_stat_from_text("have a difficult conversation about it", default_stat_id="discipline")
        assert result in ("courage", "charisma")

    def test_default_when_no_keywords(self) -> None:
        assert classify.infer_stat_from_text("zzz qqq xxx", default_stat_id="discipline") == "discipline"
        assert (
            classify.infer_stat_from_text("zzz qqq xxx", default_stat_id=None) == "courage"
        )  # first in DEFAULT_STAT_ORDER

    def test_tie_break_uses_config_order(self) -> None:
        """When scores tie, the first stat in config order wins."""
        cfg = GameConfig(
            enabled=True,
            stats=[
                StatConfig(id="vitality", name="Vitality"),
                StatConfig(id="discipline", name="Discipline"),
                StatConfig(id="knowledge", name="Knowledge"),
            ],
        )
        # "weekly" hits discipline. None of these keywords hit vitality
        # or knowledge in this single short string, so discipline
        # wins outright. Use a longer text that ties.
        # ``"weekly weekly"`` scores discipline 2, others 0 — still no tie.
        # Better: build ties explicitly by hitting multiple stats once each.
        # ``"gym weekly"`` hits vitality (gym) and discipline (weekly).
        result = classify.infer_stat_from_text("gym weekly", config=cfg, default_stat_id="discipline")
        assert result == "vitality"  # first in cfg.stats wins the tie


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


class TestLabels:
    def test_label_for_level_in_range(self) -> None:
        assert labels.label_for_level("courage", 1) == "Cowardly"
        assert labels.label_for_level("courage", 10) == "Legendary"
        assert labels.label_for_level("discipline", 5) == "Consistent"

    def test_label_for_level_out_of_range_clamps(self) -> None:
        assert labels.label_for_level("courage", 0) == "Cowardly"
        assert labels.label_for_level("courage", 99) == "Legendary"

    def test_label_for_unknown_stat_falls_back(self) -> None:
        assert labels.label_for_level("unknown", 5) == "L5"


# ---------------------------------------------------------------------------
# Source objects
# ---------------------------------------------------------------------------


class TestSources:
    def test_frozen_dataclasses(self) -> None:
        s = TaskSource(task_uid="t-1", title="foo", description=None)
        with pytest.raises((AttributeError, TypeError)):
            s.title = "bar"  # type: ignore[misc]

    def test_source_equality(self) -> None:
        a = SessionSource(session_uid="l-1", label="work", category=TimeLogCategory.WORK)
        b = SessionSource(session_uid="l-1", label="work", category=TimeLogCategory.WORK)
        assert a == b


# ---------------------------------------------------------------------------
# Game facade orchestration
# ---------------------------------------------------------------------------


class _Harness:
    """Bundle of (caldav, game facade) for tests."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.caldav = MockCalDAVFacade()
        self.facade = GameFacade(cast(CalDAVFacade, self.caldav), GameConfig(enabled=enabled))

    def make_task(self, title: str = "test", description: str | None = None) -> str:
        t = self.caldav.create_task(TaskCreate(title=title, description=description))
        return t.uid

    def make_session(
        self,
        label: str,
        category: TimeLogCategory,
        duration_minutes: int = 25,
    ) -> str:
        from genesis.entities import TimeLogStatus

        start = datetime.now(UTC) - timedelta(minutes=duration_minutes)
        end = start + timedelta(minutes=duration_minutes)
        entry = self.caldav.create_time_log_entry(
            TimeLogEntryCreate(
                label=label,
                mode=TimeLogMode.COUNTDOWN,
                start=start,
                due=end,
                completed_at=end,
                category=category,
                status=TimeLogStatus.COMPLETED,
            )
        )
        return entry.uid


class TestGameFacade:
    def test_disabled_raises(self) -> None:
        h = _Harness(enabled=False)
        with pytest.raises(GameDisabled):
            h.facade.record_xp_event(
                source=ReasonSource(reason="test"),
                stat_id="courage",
                delta=5,
                idempotency_key="k1",
            )

    def test_reason_requires_explicit(self) -> None:
        h = _Harness()
        # Missing stat_id
        with pytest.raises(ValueError):
            h.facade.record_xp_event(
                source=ReasonSource(reason="manual"),
                delta=5,
                idempotency_key="k1",
            )
        # Missing delta
        with pytest.raises(ValueError):
            h.facade.record_xp_event(
                source=ReasonSource(reason="manual"),
                stat_id="discipline",
                idempotency_key="k2",
            )

    def test_idempotency_dedup(self) -> None:
        h = _Harness()
        # First call creates the event.
        ev1 = h.facade.record_xp_event(
            source=ReasonSource(reason="manual reward"),
            stat_id="discipline",
            delta=5,
            idempotency_key="dedup-1",
        )
        # Second call with the same key returns the existing event
        # without creating a duplicate.
        ev2 = h.facade.record_xp_event(
            source=ReasonSource(reason="manual reward"),
            stat_id="discipline",
            delta=5,
            idempotency_key="dedup-1",
        )
        assert ev1 is not None
        assert ev2 is not None
        assert ev1.uid == ev2.uid
        # Only one event in storage.
        assert len(h.caldav.list_xp_events()) == 1

    def test_task_completion_infers_discipline(self) -> None:
        h = _Harness()
        task_uid = h.make_task(title="finish the paperwork")
        ev = h.facade.record_xp_event(
            source=TaskSource(task_uid=task_uid, title="finish the paperwork", description=None),
            idempotency_key=f"task:{task_uid}:completed",
        )
        assert ev is not None
        assert ev.stat_id == "discipline"
        assert ev.delta == xp.TASK_COMPLETION_XP
        assert ev.kind == XpEventKind.GAIN

    def test_session_work_infers_discipline_with_duration(self) -> None:
        h = _Harness()
        session_uid = h.make_session("deep work", TimeLogCategory.WORK, duration_minutes=50)
        ev = h.facade.record_xp_event(
            source=SessionSource(session_uid=session_uid, label="deep work", category=TimeLogCategory.WORK),
            idempotency_key=f"session:{session_uid}:stopped",
        )
        assert ev is not None
        assert ev.stat_id == "discipline"
        assert ev.delta == 2  # floor(50 / 25)
        assert ev.kind == XpEventKind.GAIN

    def test_session_workout_infers_vitality(self) -> None:
        h = _Harness()
        session_uid = h.make_session("push day", TimeLogCategory.WORKOUT, duration_minutes=60)
        ev = h.facade.record_xp_event(
            source=SessionSource(session_uid=session_uid, label="push day", category=TimeLogCategory.WORKOUT),
            idempotency_key=f"session:{session_uid}:stopped",
        )
        assert ev is not None
        assert ev.stat_id == "vitality"
        assert ev.delta == 2  # floor(60 / 25)
        assert ev.kind == XpEventKind.GAIN

    def test_session_play_is_loss(self) -> None:
        h = _Harness()
        session_uid = h.make_session("gaming", TimeLogCategory.PLAY, duration_minutes=180)
        ev = h.facade.record_xp_event(
            source=SessionSource(session_uid=session_uid, label="gaming", category=TimeLogCategory.PLAY),
            idempotency_key=f"session:{session_uid}:stopped",
        )
        assert ev is not None
        assert ev.stat_id == "discipline"
        assert ev.delta == -1  # floor(180 / 120) = 1, negated
        assert ev.kind == XpEventKind.LOSS

    def test_session_personal_no_match_raises(self) -> None:
        h = _Harness()
        session_uid = h.make_session("zzz qqq", TimeLogCategory.PERSONAL, duration_minutes=30)
        with pytest.raises(GameClassificationError):
            h.facade.record_xp_event(
                source=SessionSource(session_uid=session_uid, label="zzz qqq", category=TimeLogCategory.PERSONAL),
                idempotency_key=f"session:{session_uid}:stopped",
            )

    def test_explicit_overrides_inference(self) -> None:
        h = _Harness()
        task_uid = h.make_task(title="zzz qqq xxx")
        ev = h.facade.record_xp_event(
            source=TaskSource(task_uid=task_uid, title="zzz qqq xxx", description=None),
            stat_id="vitality",
            delta=99,
            idempotency_key=f"task:{task_uid}:completed",
        )
        assert ev is not None
        assert ev.stat_id == "vitality"
        assert ev.delta == 99

    def test_get_character_aggregates(self) -> None:
        h = _Harness()
        # Seed three events for discipline.
        for i in range(3):
            h.facade.record_xp_event(
                source=ReasonSource(reason="manual"),
                stat_id="discipline",
                delta=10,
                idempotency_key=f"k{i}",
            )
        # One event for vitality.
        h.facade.record_xp_event(
            source=ReasonSource(reason="manual"),
            stat_id="vitality",
            delta=5,
            idempotency_key="v1",
        )

        char = h.facade.get_character()
        by_stat = {p.stat_id: p for p in char.progress}
        assert by_stat["discipline"].xp == 30
        assert by_stat["vitality"].xp == 5
        assert by_stat["courage"].xp == 0
        assert len(char.recent_events) == 4

    def test_get_curve(self) -> None:
        h = _Harness()
        curve = h.facade.get_curve()
        assert curve[0] == (1, 0)
        assert curve[1] == (2, 30)
        assert curve[-1] == (xp.MAX_LEVEL, 1550)


# ---------------------------------------------------------------------------
# Facade-level orchestration methods (record_xp_event, complete_task, stop_session)
# ---------------------------------------------------------------------------


class TestFacadeOrchestration:
    def _make_facade(self):
        """Build a real `` but with ``.caldav`` swapped for the mock.

        Avoids a live Radicale dependency. The Facade's other
        sub-facades (journal, profile, backup, game) are not used
        by ``complete_task`` / ``stop_session`` / ``record_xp_event``,
        so we don't need to mock those.
        """
        import os
        import tempfile

        from genesis.core.facade import Facade

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("dav:\n  password: test-pw\n")
            path = f.name
        try:
            facade = Facade(path, ensure_server=False)
            # Replace the lazy ``caldav`` property to return a mock
            # by injecting a real instance. We can't easily patch the
            # property, but we can swap the underlying object by
            # setting ``_caldav`` after first touching it (which
            # would normally start Radicale). Instead, monkeypatch the
            # facade's own ``caldav`` attribute via the type.
            from typing import cast

            from genesis.configs import GameConfig
            from genesis.game.facade import GameFacade

            mock = MockCalDAVFacade()
            facade._caldav = mock  # type: ignore[attr-defined]
            facade._game = GameFacade(  # type: ignore[attr-defined]
                cast(CalDAVFacade, mock),
                GameConfig(enabled=True),
            )
            return facade
        finally:
            os.unlink(path)

    def test_complete_task_calls_caldav_and_fires_xp(self) -> None:
        facade = self._make_facade()
        task = facade.caldav.create_task(TaskCreate(title="finish the paperwork"))
        assert task.status == TaskStatus.NEEDS_ACTION

        updated = facade.complete_task(task.uid)
        assert updated is not None
        assert updated.status == TaskStatus.COMPLETED
        # XP event was written
        events = facade.game.list_xp_events(source_task_id=task.uid)
        assert len(events) == 1
        assert events[0].stat_id == "discipline"
        assert events[0].delta == 5

    def test_complete_task_xp_failure_does_not_rollback(self) -> None:
        """When game is disabled, complete_task still marks the task done."""
        facade = self._make_facade()
        # Disable the game layer
        from genesis.configs import GameConfig
        from genesis.game.facade import GameFacade

        facade._game = GameFacade(  # type: ignore[attr-defined]
            facade.caldav, GameConfig(enabled=False)
        )

        task = facade.caldav.create_task(TaskCreate(title="something"))
        updated = facade.complete_task(task.uid)
        assert updated is not None
        assert updated.status == TaskStatus.COMPLETED
        # No XP event written
        events = facade.game.list_xp_events()
        assert len(events) == 0

    def test_stop_session_calls_caldav_and_fires_xp(self) -> None:
        facade = self._make_facade()
        from genesis.entities import TimeLogEntryCreate, TimeLogMode, TimeLogStatus

        start = datetime.now(UTC) - timedelta(minutes=50)
        end = start + timedelta(minutes=50)
        entry = facade.caldav.create_time_log_entry(
            TimeLogEntryCreate(
                label="deep work",
                mode=TimeLogMode.COUNTDOWN,
                start=start,
                due=end,
                completed_at=end,
                category=TimeLogCategory.WORK,
                status=TimeLogStatus.COMPLETED,
            )
        )
        now = datetime.now(UTC)
        updated = facade.stop_session(
            entry.uid,
            actual_due=now,
            completed_at=now,
            category=TimeLogCategory.WORK,
        )
        assert updated is not None
        events = facade.game.list_xp_events(source_session_id=entry.uid)
        assert len(events) == 1
        assert events[0].delta == 2  # floor(50 / 25)

    def test_record_xp_event_task_dispatches(self) -> None:
        facade = self._make_facade()
        task = facade.caldav.create_task(TaskCreate(title="finish paperwork"))
        ev = facade.record_xp_event(task_id=task.uid)
        assert ev is not None
        assert ev.stat_id == "discipline"
        assert ev.delta == 5
        # Idempotent on auto-derived key
        ev2 = facade.record_xp_event(task_id=task.uid)
        assert ev2 is not None
        assert ev2.uid == ev.uid

    def test_record_xp_event_requires_exactly_one_source(self) -> None:
        facade = self._make_facade()
        with pytest.raises(ValueError):
            facade.record_xp_event()  # no source
        with pytest.raises(ValueError):
            facade.record_xp_event(
                task_id="t-x",
                reason="manual",
                stat_id="courage",
                delta=5,
                idempotency_key="k",
            )  # both

    def test_record_xp_event_missing_source_returns_none(self) -> None:
        facade = self._make_facade()
        ev = facade.record_xp_event(task_id="nonexistent")
        assert ev is None

    def test_retrospective_log_fires_xp(self) -> None:
        """Regression: the Statistics page's 'Log past session' flow
        must fire XP just like a live session stop does.

        Before this fix, ``session_log_dialog`` called
        ``caldav.create_time_log_entry(...)`` directly, bypassing
        the facade-level XP wiring. Retrospective sessions had no
        XP events.
        """
        from genesis.entities import TimeLogEntryCreate, TimeLogMode, TimeLogStatus

        facade = self._make_facade()
        start = datetime.now(UTC) - timedelta(minutes=50)
        end = start + timedelta(minutes=50)
        # Mimic what ``session_log_dialog`` does after the user's edits.
        persisted = facade.caldav.create_time_log_entry(
            TimeLogEntryCreate(
                label="deep work",
                mode=TimeLogMode.COUNTDOWN,
                start=start,
                due=end,
                completed_at=end,
                category=TimeLogCategory.WORK,
                status=TimeLogStatus.COMPLETED,
            )
        )
        assert persisted is not None
        # Mirror the dialog's XP-fire call.
        facade._fire_xp_safely(
            facade.record_xp_event,
            session_id=persisted.uid,
            occurred_at=persisted.completed_at,
        )
        events = facade.game.list_xp_events(source_session_id=persisted.uid)
        assert len(events) == 1
        assert events[0].stat_id == "discipline"
        assert events[0].delta == 2  # floor(50 / 25)

    def test_retrospective_log_complete_task_box_fires_xp(self) -> None:
        """When the dialog's 'Also mark task completed' checkbox is
        ticked, the task completion should fire XP (via
        ``facade.complete_task``, which already wires XP).
        """
        facade = self._make_facade()
        task = facade.caldav.create_task(TaskCreate(title="finish paperwork"))
        facade.complete_task(task.uid)
        events = facade.game.list_xp_events(source_task_id=task.uid)
        assert len(events) == 1
        assert events[0].stat_id == "discipline"
        assert events[0].delta == 5
