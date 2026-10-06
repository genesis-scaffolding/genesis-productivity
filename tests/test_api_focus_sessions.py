"""Unit tests for the focus session API endpoints.

Covers start / lodge / stop / discard on the productivity router,
backed by the in-memory mock facade (fast, no Radicale).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _login(client: TestClient) -> str:
    resp = client.post(
        "/auth/login",
        data={"username": "default", "password": "test-pw", "grant_type": "password"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def token(api_server: TestClient) -> str:
    return _login(api_server)


def test_start_countdown_session(api_server: TestClient, token: str) -> None:
    resp = api_server.post(
        "/productivity/time_log",
        json={"label": "deep work", "mode": "countdown", "duration_minutes": 25, "category": "work"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["label"] == "deep work"
    assert body["mode"] == "countdown"
    assert body["status"] == "in-progress"
    assert body["category"] == "work"
    assert body["due"] is not None


def test_start_countup_session(api_server: TestClient, token: str) -> None:
    resp = api_server.post(
        "/productivity/time_log",
        json={"label": "open work", "mode": "countup"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["mode"] == "countup"
    assert body["due"] is None


def test_start_countdown_without_duration_rejected(api_server: TestClient, token: str) -> None:
    resp = api_server.post(
        "/productivity/time_log",
        json={"label": "no duration", "mode": "countdown"},
        headers=_auth(token),
    )
    assert resp.status_code == 400


def test_lodge_completed_session(api_server: TestClient, token: str) -> None:
    resp = api_server.post(
        "/productivity/time_log/completed",
        json={
            "label": "morning focus",
            "start": "2026-02-10T09:00:00.000Z",
            "duration_minutes": 25,
            "category": "other",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["mode"] == "countdown"
    assert body["completed_at"] is not None
    assert body["category"] == "other"


def test_lodge_requires_positive_duration(api_server: TestClient, token: str) -> None:
    resp = api_server.post(
        "/productivity/time_log/completed",
        json={"label": "bad", "start": "2026-02-10T09:00:00.000Z", "duration_minutes": 0},
        headers=_auth(token),
    )
    assert resp.status_code == 422


def test_stop_session_fires_xp(api_server: TestClient, token: str) -> None:
    start = api_server.post(
        "/productivity/time_log",
        json={"label": "deep work", "mode": "countdown", "duration_minutes": 25, "category": "work"},
        headers=_auth(token),
    )
    assert start.status_code == 201
    uid = start.json()["uid"]

    stop = api_server.post(f"/productivity/time_log/{uid}/stop", json={}, headers=_auth(token))
    assert stop.status_code == 200, stop.text
    body = stop.json()
    assert body["status"] == "completed"
    assert body["completed_at"] is not None

    # The XP event was fired for the stopped session.
    xp = api_server.get("/game/character/xp-events", headers=_auth(token))
    assert xp.status_code == 200
    assert any(e.get("source_session_id") == uid for e in xp.json()["items"])


def test_stop_session_accepts_category_override(api_server: TestClient, token: str) -> None:
    start = api_server.post(
        "/productivity/time_log",
        json={"label": "flex", "mode": "countup", "category": "work"},
        headers=_auth(token),
    )
    uid = start.json()["uid"]
    stop = api_server.post(
        f"/productivity/time_log/{uid}/stop",
        json={"category": "workout"},
        headers=_auth(token),
    )
    assert stop.status_code == 200
    assert stop.json()["category"] == "workout"


def test_stop_completed_session_conflicts(api_server: TestClient, token: str) -> None:
    lodge = api_server.post(
        "/productivity/time_log/completed",
        json={"label": "past", "start": "2026-02-10T09:00:00.000Z", "duration_minutes": 25},
        headers=_auth(token),
    )
    uid = lodge.json()["uid"]
    stop = api_server.post(f"/productivity/time_log/{uid}/stop", json={}, headers=_auth(token))
    assert stop.status_code == 409


def test_stop_missing_session_404(api_server: TestClient, token: str) -> None:
    stop = api_server.post("/productivity/time_log/nope123/stop", json={}, headers=_auth(token))
    assert stop.status_code == 404


def test_discard_session(api_server: TestClient, token: str) -> None:
    start = api_server.post(
        "/productivity/time_log",
        json={"label": "throwaway", "mode": "countup"},
        headers=_auth(token),
    )
    uid = start.json()["uid"]
    resp = api_server.delete(f"/productivity/time_log/{uid}", headers=_auth(token))
    assert resp.status_code == 204
    gone = api_server.get(f"/productivity/time_log/{uid}", headers=_auth(token))
    assert gone.status_code == 404


def test_discard_missing_session_404(api_server: TestClient, token: str) -> None:
    resp = api_server.delete("/productivity/time_log/nope123", headers=_auth(token))
    assert resp.status_code == 404


def test_start_with_unknown_task_id_rejected(api_server: TestClient, token: str) -> None:
    resp = api_server.post(
        "/productivity/time_log",
        json={"label": "orphan", "mode": "countup", "task_id": "nope123"},
        headers=_auth(token),
    )
    assert resp.status_code == 400
    assert "task_id" in resp.json()["detail"]


def test_lodge_with_unknown_task_id_rejected(api_server: TestClient, token: str) -> None:
    resp = api_server.post(
        "/productivity/time_log/completed",
        json={
            "label": "orphan",
            "start": "2026-02-10T09:00:00.000Z",
            "duration_minutes": 25,
            "task_id": "nope123",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 400


def test_start_with_known_task_id_accepted(api_server: TestClient, token: str) -> None:
    task = api_server.post(
        "/productivity/tasks",
        json={"title": "focus on me"},
        headers=_auth(token),
    ).json()
    resp = api_server.post(
        "/productivity/time_log",
        json={"label": "aligned", "mode": "countup", "task_id": task["uid"]},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["task_id"] == task["uid"]


def test_active_sessions_visible_via_time_log(api_server: TestClient, token: str) -> None:
    api_server.post(
        "/productivity/time_log",
        json={"label": "running", "mode": "countdown", "duration_minutes": 25},
        headers=_auth(token),
    )
    api_server.post(
        "/productivity/time_log/completed",
        json={"label": "done", "start": "2026-02-10T09:00:00.000Z", "duration_minutes": 25},
        headers=_auth(token),
    )
    resp = api_server.get("/productivity/time_log?active_only=true", headers=_auth(token))
    assert resp.status_code == 200
    labels = [e["label"] for e in resp.json()["items"]]
    assert "running" in labels
    assert "done" not in labels
