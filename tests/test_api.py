"""Integration tests for the agent API.

These spin up a real Radicale + a FastAPI TestClient that talks to
it via the real ``genesis.api.app``. They cover the full
HTTP-to-storage round trip for projects, tasks, and journals, plus
the auth flow.

Marked ``@pytest.mark.integration`` per project convention.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


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


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_login_success(api_server: TestClient) -> None:
    resp = api_server.post(
        "/auth/login",
        data={"username": "default", "password": "test-pw", "grant_type": "password"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] >= 36000
    assert body["access_token"].count(".") == 2  # header.payload.sig
    assert body["refresh_token"].count(".") == 2


def test_login_wrong_password_rejected(api_server: TestClient) -> None:
    resp = api_server.post(
        "/auth/login",
        data={"username": "default", "password": "wrong", "grant_type": "password"},
    )
    assert resp.status_code == 401


def test_productivity_requires_bearer_token(api_server: TestClient) -> None:
    resp = api_server.get("/productivity/projects")
    assert resp.status_code == 401


def test_refresh_exchanges_valid_token(api_server: TestClient) -> None:
    login = api_server.post(
        "/auth/login",
        data={"username": "default", "password": "test-pw", "grant_type": "password"},
    )
    refresh = login.json()["refresh_token"]
    resp = api_server.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"].count(".") == 2


def test_refresh_with_access_token_is_rejected(api_server: TestClient) -> None:
    login = api_server.post(
        "/auth/login",
        data={"username": "default", "password": "test-pw", "grant_type": "password"},
    )
    access = login.json()["access_token"]
    resp = api_server.post("/auth/refresh", json={"refresh_token": access})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_project_crud_round_trip(api_server_real: TestClient) -> None:
    token = _login(api_server_real)

    created = api_server_real.post(
        "/productivity/projects",
        json={"name": "Test Project", "description": "Hello"},
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["uid"]
    assert project["name"] == "Test Project"
    assert project["description"] == "Hello"
    assert project["status"] == "needs-action"
    assert project["created"] is not None

    fetched = api_server_real.get(
        f"/productivity/projects/{project['uid']}",
        headers=_auth(token),
    )
    assert fetched.status_code == 200
    assert fetched.json()["uid"] == project["uid"]

    patched = api_server_real.patch(
        f"/productivity/projects/{project['uid']}",
        json={"status": "in-progress"},
        headers=_auth(token),
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "in-progress"

    listing = api_server_real.get("/productivity/projects", headers=_auth(token))
    uids = [p["uid"] for p in listing.json()]
    assert project["uid"] in uids

    deleted = api_server_real.delete(
        f"/productivity/projects/{project['uid']}",
        headers=_auth(token),
    )
    assert deleted.status_code == 204

    missing = api_server_real.get(
        f"/productivity/projects/{project['uid']}",
        headers=_auth(token),
    )
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_task_create_link_unlink_round_trip(api_server_real: TestClient) -> None:
    token = _login(api_server_real)

    project = api_server_real.post(
        "/productivity/projects",
        json={"name": "Container"},
        headers=_auth(token),
    ).json()

    task = api_server_real.post(
        "/productivity/tasks",
        json={"title": "linked", "project_id": project["uid"]},
        headers=_auth(token),
    ).json()
    assert task["uid"]
    assert task["title"] == "linked"
    assert task["project_id"] == project["uid"]
    assert task["status"] == "needs-action"

    listing = api_server_real.get(
        "/productivity/tasks?project_id=" + project["uid"],
        headers=_auth(token),
    ).json()
    assert listing["total"] == 1
    assert listing["items"][0]["uid"] == task["uid"]
    assert listing["items"][0]["project_id"] == project["uid"]

    unlinked = api_server_real.patch(
        f"/productivity/tasks/{task['uid']}",
        json={"project_id": None, "clear_project_id": True},
        headers=_auth(token),
    ).json()
    assert unlinked["project_id"] is None


@pytest.mark.integration
def test_task_update_status_to_completed(api_server_real: TestClient) -> None:
    token = _login(api_server_real)
    task = api_server_real.post(
        "/productivity/tasks",
        json={"title": "to do"},
        headers=_auth(token),
    ).json()

    completed = api_server_real.patch(
        f"/productivity/tasks/{task['uid']}",
        json={"status": "completed"},
        headers=_auth(token),
    ).json()
    assert completed["status"] == "completed"


@pytest.mark.integration
def test_task_datetime_serializes_as_z_with_3_decimals(api_server_real: TestClient) -> None:
    token = _login(api_server_real)
    # Sub-second precision is dropped on the VTODO boundary (iCal DUE
    # only stores seconds). The wire format always emits
    # ``.mmmZ`` even when the underlying value is whole-second.
    task = api_server_real.post(
        "/productivity/tasks",
        json={
            "title": "with deadline",
            "hard_deadline": "2026-09-07T14:30:05Z",
        },
        headers=_auth(token),
    ).json()
    fetched = api_server_real.get(
        f"/productivity/tasks/{task['uid']}",
        headers=_auth(token),
    ).json()
    assert fetched["hard_deadline"] == "2026-09-07T14:30:05.000Z"


def test_task_404_on_missing_uid(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.get(
        "/productivity/tasks/does-not-exist",
        headers=_auth(token),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Journals
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_journal_crud_round_trip(api_server_real: TestClient) -> None:
    token = _login(api_server_real)

    created = api_server_real.post(
        "/productivity/journals",
        json={
            "type": "misc",
            "date": "2026-09-07",
            "title": "smoke",
            "content": "body",
        },
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    entry = created.json()
    assert entry["uid"]
    assert entry["type"] == "misc"
    assert entry["title"] == "smoke"

    fetched = api_server_real.get(
        f"/productivity/journals/{entry['uid']}",
        headers=_auth(token),
    ).json()
    assert fetched["content"] == "body"
    assert fetched["type"] == "misc"

    patched = api_server_real.patch(
        f"/productivity/journals/{entry['uid']}",
        json={"title": "renamed"},
        headers=_auth(token),
    ).json()
    assert patched["title"] == "renamed"

    listing = api_server_real.get(
        "/productivity/journals",
        headers=_auth(token),
    ).json()
    uids = [j["uid"] for j in listing["items"]]
    assert entry["uid"] in uids

    deleted = api_server_real.delete(
        f"/productivity/journals/{entry['uid']}",
        headers=_auth(token),
    )
    assert deleted.status_code == 204


@pytest.mark.integration
def test_journal_with_project_id_creates_footer(api_server_real: TestClient) -> None:
    """Round-trip project_id via the > Project: {name} footer."""
    token = _login(api_server_real)
    project = api_server_real.post(
        "/productivity/projects",
        json={"name": "footer-test"},
        headers=_auth(token),
    ).json()

    entry = api_server_real.post(
        "/productivity/journals",
        json={
            "type": "project",
            "date": "2026-09-07",
            "content": "work notes",
            "project_id": project["uid"],
        },
        headers=_auth(token),
    ).json()
    assert entry["project_id"] == project["uid"]

    # The body has the footer stripped for the wire response; the
    # stored file keeps it.
    assert "> Project: footer-test" not in entry["content"]

    # And it's recoverable on read.
    fetched = api_server_real.get(
        f"/productivity/journals/{entry['uid']}",
        headers=_auth(token),
    ).json()
    assert fetched["project_id"] == project["uid"]


@pytest.mark.integration
def test_journal_clear_project_id_strips_footer(api_server_real: TestClient) -> None:
    token = _login(api_server_real)
    project = api_server_real.post(
        "/productivity/projects",
        json={"name": "strip-test"},
        headers=_auth(token),
    ).json()

    entry = api_server_real.post(
        "/productivity/journals",
        json={
            "type": "project",
            "date": "2026-09-07",
            "content": "x",
            "project_id": project["uid"],
        },
        headers=_auth(token),
    ).json()

    cleared = api_server_real.patch(
        f"/productivity/journals/{entry['uid']}",
        json={"clear_project_id": True},
        headers=_auth(token),
    ).json()
    assert cleared["project_id"] is None
