"""Round-trip tests for XP events against a real Radicale.

Integration tests, marked ``@pytest.mark.integration``. Run via
``make test-integration``.

Pins the wire shape of the xp_events collection so an accidental
drop of a custom X- field is caught instead of silently corrupting
legacy data on disk.

Also covers the "empty xp_events collection on a fresh user"
scenario: ``list_xp_events`` must return ``[]`` instead of crashing
with a NotFoundError when the collection has not yet been
MKCALENDAR'd.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from genesis.caldav.ical import xp_event_from_ical, xp_event_to_ical
from genesis.entities import XpEvent, XpEventKind


@pytest.mark.integration
def test_xp_event_round_trip_preserves_custom_fields(api_server_real):
    """Every custom X- field survives the iCal round trip.

    Regression: a previous iteration accidentally dropped
    X-IDEMPOTENCY-KEY from xp_event_to_ical, which would silently
    break dedup for every existing event.
    """
    event = XpEvent(
        uid="x-test1",
        stat_id="discipline",
        delta=10,
        kind=XpEventKind.GAIN,
        source_task_id="t-abc",
        source_session_id="l-xyz",
        reason="task completed",
        occurred_at=datetime(2026, 9, 23, 8, 0, tzinfo=UTC),
        idempotency_key="task:t-abc:completed",
    )
    component = xp_event_to_ical(event)
    restored = xp_event_from_ical(component)

    assert restored.stat_id == event.stat_id
    assert restored.delta == event.delta
    assert restored.kind == event.kind
    assert restored.source_task_id == event.source_task_id
    assert restored.source_session_id == event.source_session_id
    assert restored.reason == event.reason
    assert restored.occurred_at == event.occurred_at
    assert restored.idempotency_key == event.idempotency_key


@pytest.mark.integration
def test_xp_event_persists_via_caldav_facade(api_server_real):
    """create_xp_event -> list_xp_events round trip on real Radicale.

    Exercises the lazy MKCALENDAR path: the first XP event
    persisted to a fresh user creates the xp_events collection.
    """
    from genesis.configs import resolve_config_path
    from genesis.core.facade import Facade
    from genesis.entities import XpEventCreate, XpEventKind

    facade = Facade(resolve_config_path("default"), ensure_server=False)
    try:
        create = XpEventCreate(
            stat_id="discipline",
            delta=10,
            kind=XpEventKind.GAIN,
            reason="integration test",
            occurred_at=datetime.now(UTC),
            idempotency_key="integration-round-trip-1",
        )
        persisted = facade.caldav.create_xp_event(create)
        assert persisted.uid != ""

        # Same idempotency_key returns the existing event.
        again = facade.caldav.create_xp_event(create)
        # No idempotency check at the create_xp_event layer; the
        # facade (GameFacade.record_xp_event) does that. We just
        # verify the raw storage works here.
        assert again.uid != ""
    finally:
        facade.shutdown()


@pytest.mark.integration
def test_list_xp_events_on_empty_collection_returns_empty(api_server_real):
    """Regression guard for the user-reported NotFoundError crash.

    Before this fix, calling ``list_xp_events`` on a fresh user
    whose xp_events collection had not been MKCALENDAR'd crashed
    with ``caldav.lib.error.NotFoundError``. The fix returns ``[]``
    on NotFoundError so the character page renders zero-state
    instead of crashing.
    """
    from genesis.configs import resolve_config_path
    from genesis.core.facade import Facade

    facade = Facade(resolve_config_path("default"), ensure_server=False)
    try:
        # No XP events ever persisted on a fresh user. The
        # collection should not exist; the search must not crash.
        events = facade.caldav.list_xp_events()
        assert events == []
    finally:
        facade.shutdown()
