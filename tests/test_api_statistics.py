"""Unit tests for the ``/productivity/statistics`` endpoint.

Uses the fast ``api_server`` and ``api_server_with_mock`` fixtures
from ``conftest.py``. The endpoint's aggregation logic is
exhaustively covered in ``tests/test_stats.py``; this file covers
wire-shape and auth behaviour.

The ``api_server_with_mock`` fixture exposes the underlying
``MockCalDAVFacade`` so tests can seed time log entries — there is no
``POST /productivity/time_log`` endpoint, only the read-only list and
detail endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from genesis.category.enums import TimeLogCategory
from genesis.entities import (
    TaskCreate,
    TimeLogEntry,
    TimeLogEntryCreate,
    TimeLogMode,
    TimeLogStatus,
)
from tests.mock_caldav import MockCalDAVFacade


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


def _seed_session(
    mock: MockCalDAVFacade,
    *,
    label: str,
    start: datetime,
    duration_minutes: int,
    task_id: str | None = None,
    category: TimeLogCategory = TimeLogCategory.OTHER,
) -> TimeLogEntry:
    return mock.create_time_log_entry(
        TimeLogEntryCreate(
            label=label,
            start=start,
            due=start + timedelta(minutes=duration_minutes),
            status=TimeLogStatus.COMPLETED,
            mode=TimeLogMode.COUNTDOWN,
            task_id=task_id,
            category=category,
        )
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_statistics_requires_bearer_token(api_server: TestClient) -> None:
    resp = api_server.get("/productivity/statistics")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Response shape and defaults
# ---------------------------------------------------------------------------


def test_statistics_default_period_is_this_week(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.get(
        "/productivity/statistics",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["period"] == "this-week"
    for key in ("kpis", "daily", "by_task", "recent"):
        assert key in body


def test_statistics_with_one_completed_session(
    api_server_with_mock: tuple[TestClient, MockCalDAVFacade],
) -> None:
    client, mock = api_server_with_mock
    token = _login(client)
    headers = _auth(token)

    task = mock.create_task(TaskCreate(title="Write spec"))
    # Pin start to today so the session falls in the "this-week" window
    # regardless of when the test runs (a hardcoded date would drift
    # out of the window over time).
    start = datetime.now(UTC).replace(hour=10, minute=0, second=0, microsecond=0)
    _seed_session(
        mock,
        label="Write spec",
        start=start,
        duration_minutes=25,
        task_id=task.uid,
        category=TimeLogCategory.WORK,
    )

    resp = client.get(
        "/productivity/statistics?period=this-week",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kpis"]["session_count"] == 1
    assert body["kpis"]["total_minutes"] == 25.0
    assert body["kpis"]["tasks_touched"] == 1
    assert len(body["by_task"]) == 1
    assert body["by_task"][0]["title"] == "Write spec"
    assert body["by_task"][0]["count"] == 1
    assert body["by_task"][0]["total_seconds"] == 1500.0
    assert len(body["recent"]) == 1
    assert body["recent"][0]["uid"]  # non-empty
    assert body["recent"][0]["label"] == "Write spec"
    assert body["recent"][0]["task_title"] == "Write spec"
    assert body["recent"][0]["mode"] == "countdown"
    assert body["recent"][0]["duration_seconds"] == 1500.0
    # ISO format from UtcDateTime: trailing Z with 3 decimals.
    assert body["recent"][0]["start"].endswith("Z")
    # Category breakdown surfaces the entry's category.
    assert body["kpis"]["minutes_by_category"]["work"] == 25.0
    assert body["kpis"]["minutes_by_category"]["other"] == 0.0
    assert any(b["category"] == "work" and b["total_seconds"] == 1500.0 for b in body["by_category"])
    # Every category is present, with the four unused ones at zero.
    cats = {b["category"] for b in body["by_category"]}
    assert cats == {c.value for c in TimeLogCategory}


# ---------------------------------------------------------------------------
# Period selector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "period",
    ["this-week", "this-month", "last-30-days", "all-time"],
)
def test_statistics_accepts_each_period_label(api_server: TestClient, period: str) -> None:
    token = _login(api_server)
    resp = api_server.get(
        f"/productivity/statistics?period={period}",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["period"] == period


def test_statistics_rejects_unknown_period(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.get(
        "/productivity/statistics?period=bogus",
        headers=_auth(token),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# top_n and recent_limit overrides
# ---------------------------------------------------------------------------


def test_statistics_top_n_caps_by_task(
    api_server_with_mock: tuple[TestClient, MockCalDAVFacade],
) -> None:
    client, mock = api_server_with_mock
    token = _login(client)
    base = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)
    for i in range(15):
        task = mock.create_task(TaskCreate(title=f"Task {i}"))
        s = base + timedelta(minutes=i * 30)
        _seed_session(
            mock,
            label=task.title,
            start=s,
            duration_minutes=15,
            task_id=task.uid,
        )

    resp = client.get(
        "/productivity/statistics?period=all-time&top_n=3",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["by_task"]) == 3


def test_statistics_recent_limit_caps_recent(
    api_server_with_mock: tuple[TestClient, MockCalDAVFacade],
) -> None:
    client, mock = api_server_with_mock
    token = _login(client)
    base = datetime(2026, 9, 7, 10, 0, tzinfo=UTC)
    for i in range(25):
        s = base + timedelta(hours=i)
        _seed_session(
            mock,
            label=f"session-{i}",
            start=s,
            duration_minutes=10,
        )

    resp = client.get(
        "/productivity/statistics?period=all-time&recent_limit=5",
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["recent"]) == 5


def test_statistics_top_n_bounds_rejected(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.get(
        "/productivity/statistics?top_n=0",
        headers=_auth(token),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------


def test_statistics_empty_when_no_completed_sessions(
    api_server: TestClient,
) -> None:
    token = _login(api_server)
    resp = api_server.get(
        "/productivity/statistics",
        headers=_auth(token),
    )
    body = resp.json()
    assert body["kpis"]["session_count"] == 0
    assert body["kpis"]["total_minutes"] == 0.0
    assert body["kpis"]["avg_minutes"] == 0.0
    assert body["by_task"] == []
    assert body["recent"] == []
    # Daily still spans the period.
    assert len(body["daily"]) > 0
