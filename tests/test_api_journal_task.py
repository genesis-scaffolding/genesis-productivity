"""API tests for task-linked journal entries.

Covers the journal wire contract for the task link: create with a
task, read it back, filter the list by task, PATCH the link, clear
it, and the rejection of a link to a task that does not exist.

These run against the mock-backed ``api_server`` fixture, so they are
fast and need no Radicale.
"""

from __future__ import annotations

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


def _create_task(client: TestClient, token: str, title: str = "task") -> str:
    resp = client.post("/productivity/tasks", json={"title": title}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()["uid"]


def test_journal_create_with_task_id(api_server: TestClient) -> None:
    token = _login(api_server)
    task_uid = _create_task(api_server, token)

    created = api_server.post(
        "/productivity/journals",
        json={
            "type": "task",
            "date": "2026-09-05",
            "title": "notes",
            "content": "body",
            "task_id": task_uid,
        },
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["task_id"] == task_uid
    assert body["type"] == "task"


def test_journal_create_without_task_id_has_none(api_server: TestClient) -> None:
    token = _login(api_server)
    created = api_server.post(
        "/productivity/journals",
        json={"type": "misc", "date": "2026-09-05", "content": "body"},
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    assert created.json()["task_id"] is None


def test_journal_create_with_unknown_task_id_is_rejected(api_server: TestClient) -> None:
    token = _login(api_server)
    created = api_server.post(
        "/productivity/journals",
        json={
            "type": "task",
            "date": "2026-09-05",
            "content": "body",
            "task_id": "t-does-not-exist",
        },
        headers=_auth(token),
    )
    assert created.status_code == 400, created.text
    assert "unknown task_id" in created.json()["detail"]


def test_journal_read_returns_task_id(api_server: TestClient) -> None:
    token = _login(api_server)
    task_uid = _create_task(api_server, token)
    created = api_server.post(
        "/productivity/journals",
        json={
            "type": "task",
            "date": "2026-09-05",
            "content": "body",
            "task_id": task_uid,
        },
        headers=_auth(token),
    ).json()

    fetched = api_server.get(f"/productivity/journals/{created['uid']}", headers=_auth(token))
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["task_id"] == task_uid


def test_journal_list_filters_by_task_id(api_server: TestClient) -> None:
    token = _login(api_server)
    first = _create_task(api_server, token, "first")
    second = _create_task(api_server, token, "second")

    api_server.post(
        "/productivity/journals",
        json={"type": "task", "date": "2026-09-01", "content": "a", "task_id": first},
        headers=_auth(token),
    )
    api_server.post(
        "/productivity/journals",
        json={"type": "task", "date": "2026-09-02", "content": "b", "task_id": second},
        headers=_auth(token),
    )
    api_server.post(
        "/productivity/journals",
        json={"type": "misc", "date": "2026-09-03", "content": "c"},
        headers=_auth(token),
    )

    resp = api_server.get(f"/productivity/journals?task_id={first}", headers=_auth(token)).json()
    assert resp["total"] == 1
    assert resp["items"][0]["task_id"] == first

    resp = api_server.get("/productivity/journals?task_id=t-unknown", headers=_auth(token)).json()
    assert resp["total"] == 0
    assert resp["items"] == []


def test_journal_list_task_filter_ignores_type(api_server: TestClient) -> None:
    token = _login(api_server)
    task_uid = _create_task(api_server, token)
    api_server.post(
        "/productivity/journals",
        json={"type": "daily", "date": "2026-09-01", "content": "a", "task_id": task_uid},
        headers=_auth(token),
    )

    resp = api_server.get(f"/productivity/journals?task_id={task_uid}", headers=_auth(token)).json()
    assert resp["total"] == 1
    assert resp["items"][0]["type"] == "daily"


def test_journal_patch_sets_task_id(api_server: TestClient) -> None:
    token = _login(api_server)
    task_uid = _create_task(api_server, token)
    created = api_server.post(
        "/productivity/journals",
        json={"type": "misc", "date": "2026-09-05", "content": "body"},
        headers=_auth(token),
    ).json()

    patched = api_server.patch(
        f"/productivity/journals/{created['uid']}",
        json={"task_id": task_uid},
        headers=_auth(token),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["task_id"] == task_uid


def test_journal_patch_preserves_task_id(api_server: TestClient) -> None:
    token = _login(api_server)
    task_uid = _create_task(api_server, token)
    created = api_server.post(
        "/productivity/journals",
        json={"type": "task", "date": "2026-09-05", "content": "body", "task_id": task_uid},
        headers=_auth(token),
    ).json()

    patched = api_server.patch(
        f"/productivity/journals/{created['uid']}",
        json={"title": "renamed"},
        headers=_auth(token),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["task_id"] == task_uid


def test_journal_patch_clears_task_id(api_server: TestClient) -> None:
    token = _login(api_server)
    task_uid = _create_task(api_server, token)
    created = api_server.post(
        "/productivity/journals",
        json={"type": "task", "date": "2026-09-05", "content": "body", "task_id": task_uid},
        headers=_auth(token),
    ).json()

    patched = api_server.patch(
        f"/productivity/journals/{created['uid']}",
        json={"clear_task_id": True},
        headers=_auth(token),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["task_id"] is None


def test_journal_patch_with_unknown_task_id_is_rejected(api_server: TestClient) -> None:
    token = _login(api_server)
    created = api_server.post(
        "/productivity/journals",
        json={"type": "misc", "date": "2026-09-05", "content": "body"},
        headers=_auth(token),
    ).json()

    patched = api_server.patch(
        f"/productivity/journals/{created['uid']}",
        json={"task_id": "t-does-not-exist"},
        headers=_auth(token),
    )
    assert patched.status_code == 400, patched.text
    assert "unknown task_id" in patched.json()["detail"]


def test_journal_link_survives_task_rename(api_server: TestClient) -> None:
    # The link is the task UID, so renaming the task keeps the journal
    # entry attached to it.
    token = _login(api_server)
    task_uid = _create_task(api_server, token, "before")
    created = api_server.post(
        "/productivity/journals",
        json={"type": "task", "date": "2026-09-05", "content": "body", "task_id": task_uid},
        headers=_auth(token),
    ).json()

    renamed = api_server.patch(
        f"/productivity/tasks/{task_uid}",
        json={"title": "after"},
        headers=_auth(token),
    )
    assert renamed.status_code == 200, renamed.text

    fetched = api_server.get(f"/productivity/journals/{created['uid']}", headers=_auth(token)).json()
    assert fetched["task_id"] == task_uid


def test_journal_link_survives_task_delete(api_server: TestClient) -> None:
    # Deleting a task does not delete or unlink the journal entries
    # that point at it.
    token = _login(api_server)
    task_uid = _create_task(api_server, token)
    created = api_server.post(
        "/productivity/journals",
        json={"type": "task", "date": "2026-09-05", "content": "body", "task_id": task_uid},
        headers=_auth(token),
    ).json()

    deleted = api_server.delete(f"/productivity/tasks/{task_uid}", headers=_auth(token))
    assert deleted.status_code == 204, deleted.text

    fetched = api_server.get(f"/productivity/journals/{created['uid']}", headers=_auth(token))
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["task_id"] == task_uid
