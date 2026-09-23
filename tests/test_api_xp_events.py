"""API tests for the game layer.

Covers the /game/character/xp-events endpoint filters and the
POST /game/character/xp-events endpoint. Uses the in-memory
``api_server_with_mock`` fixture so the tests stay fast (no
Radicale subprocess).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tests.mock_caldav import MockCalDAVFacade

# Local copy of the helpers; the test_api.py versions are private.
# We re-login per test so the fixture's per-user state is isolated.


def _login(client: TestClient) -> str:
    resp = client.post(
        "/auth/login",
        data={
            "username": "default",
            "password": "test-pw",
            "grant_type": "password",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_xp(
    mock: MockCalDAVFacade,
    *,
    stat_id: str,
    delta: int,
    occurred_at: datetime,
    idempotency_key: str,
) -> None:
    """Seed one XP event directly via the mock."""
    from genesis.entities import XpEventCreate, XpEventKind

    mock.create_xp_event(
        XpEventCreate(
            stat_id=stat_id,
            delta=delta,
            kind=XpEventKind.GAIN if delta >= 0 else XpEventKind.LOSS,
            occurred_at=occurred_at,
            idempotency_key=idempotency_key,
            reason="seed",
        )
    )


# ---------------------------------------------------------------------------
# /character/xp-events filters
# ---------------------------------------------------------------------------


def test_xp_events_since_until_filters_by_window(
    api_server_with_mock: tuple[TestClient, MockCalDAVFacade],
) -> None:
    """``since`` / ``until`` filter the result by occurred_at."""
    client, mock = api_server_with_mock
    token = _login(client)
    headers = _auth(token)

    # Three events on different days: -3 days, today, +3 days.
    now = datetime.now(UTC)
    _seed_xp(mock, stat_id="discipline", delta=5, occurred_at=now - timedelta(days=3), idempotency_key="k1")
    _seed_xp(mock, stat_id="discipline", delta=10, occurred_at=now, idempotency_key="k2")
    _seed_xp(mock, stat_id="vitality", delta=20, occurred_at=now + timedelta(days=3), idempotency_key="k3")

    # Window covers only "today" (+/- a few hours).
    window_start = (now - timedelta(hours=1)).isoformat()
    window_end = (now + timedelta(hours=1)).isoformat()
    resp = client.get(
        "/game/character/xp-events",
        params={"since": window_start, "until": window_end},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["delta"] == 10
    assert body["items"][0]["stat_id"] == "discipline"


def test_xp_events_since_only_returns_open_window(
    api_server_with_mock: tuple[TestClient, MockCalDAVFacade],
) -> None:
    """``since`` without ``until`` returns all events from then on."""
    client, mock = api_server_with_mock
    token = _login(client)
    headers = _auth(token)

    now = datetime.now(UTC)
    _seed_xp(mock, stat_id="discipline", delta=5, occurred_at=now - timedelta(days=10), idempotency_key="old")
    _seed_xp(mock, stat_id="discipline", delta=10, occurred_at=now - timedelta(days=2), idempotency_key="recent")

    since = (now - timedelta(days=5)).isoformat()
    resp = client.get(
        "/game/character/xp-events",
        params={"since": since},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["idempotency_key"] == "recent"


def test_xp_events_until_only_returns_events_before(
    api_server_with_mock: tuple[TestClient, MockCalDAVFacade],
) -> None:
    """``until`` without ``since`` returns all events strictly before."""
    client, mock = api_server_with_mock
    token = _login(client)
    headers = _auth(token)

    now = datetime.now(UTC)
    _seed_xp(mock, stat_id="discipline", delta=5, occurred_at=now - timedelta(days=10), idempotency_key="old")
    _seed_xp(mock, stat_id="discipline", delta=10, occurred_at=now - timedelta(days=2), idempotency_key="recent")

    until = (now - timedelta(days=5)).isoformat()
    resp = client.get(
        "/game/character/xp-events",
        params={"until": until},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["idempotency_key"] == "old"


def test_xp_events_no_filters_returns_all(
    api_server_with_mock: tuple[TestClient, MockCalDAVFacade],
) -> None:
    """Without since/until, returns all events sorted newest first."""
    client, mock = api_server_with_mock
    token = _login(client)
    headers = _auth(token)

    now = datetime.now(UTC)
    _seed_xp(mock, stat_id="discipline", delta=5, occurred_at=now - timedelta(days=2), idempotency_key="a")
    _seed_xp(mock, stat_id="vitality", delta=10, occurred_at=now - timedelta(days=1), idempotency_key="b")
    _seed_xp(mock, stat_id="courage", delta=15, occurred_at=now, idempotency_key="c")

    resp = client.get("/game/character/xp-events", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    # Newest first by default.
    assert [e["idempotency_key"] for e in body["items"]] == ["c", "b", "a"]


# ---------------------------------------------------------------------------
# POST /character/xp-events
# ---------------------------------------------------------------------------


def test_create_xp_event_via_api(
    api_server_with_mock: tuple[TestClient, MockCalDAVFacade],
) -> None:
    """Reason-based XP event creation via the wire endpoint."""
    client, mock = api_server_with_mock
    token = _login(client)
    headers = _auth(token)

    resp = client.post(
        "/game/character/xp-events",
        json={
            "reason": "manual reward",
            "stat_id": "discipline",
            "delta": 25,
            "idempotency_key": "api-test-1",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["stat_id"] == "discipline"
    assert body["delta"] == 25
    assert body["kind"] == "gain"
    assert body["idempotency_key"] == "api-test-1"


def test_create_xp_event_reason_without_idempotency_key_returns_400(
    api_server_with_mock: tuple[TestClient, MockCalDAVFacade],
) -> None:
    """Reason source requires an explicit idempotency_key."""
    client, mock = api_server_with_mock
    token = _login(client)
    headers = _auth(token)

    resp = client.post(
        "/game/character/xp-events",
        json={
            "reason": "manual",
            "stat_id": "discipline",
            "delta": 5,
        },
        headers=headers,
    )
    assert resp.status_code == 400, resp.text


def test_create_xp_event_game_disabled_returns_403(
    api_server_with_mock: tuple[TestClient, MockCalDAVFacade],
) -> None:
    """When game.enabled is False, the API returns 403 not 500."""
    client, mock = api_server_with_mock
    token = _login(client)
    headers = _auth(token)

    # The mock's ``configs.game.enabled`` is what the facade reads.
    # Flip it off, run the request, flip back on.
    original_enabled = mock.configs.game.enabled  # type: ignore[attr-defined]
    mock.configs.game.enabled = False  # type: ignore[attr-defined]
    try:
        resp = client.post(
            "/game/character/xp-events",
            json={
                "reason": "manual",
                "stat_id": "discipline",
                "delta": 5,
                "idempotency_key": "x",
            },
            headers=headers,
        )
        assert resp.status_code == 403, resp.text
    finally:
        mock.configs.game.enabled = original_enabled  # type: ignore[attr-defined]
