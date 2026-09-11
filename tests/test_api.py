"""Integration tests for the agent API.

These spin up a real Radicale + a FastAPI TestClient that talks to
it via the real ``genesis.api.app``. They cover the full
HTTP-to-storage round trip for projects, tasks, and journals, plus
the auth flow.

Marked ``@pytest.mark.integration`` per project convention.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .mock_caldav import MockCalDAVFacade


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


def test_login_unknown_user_rejected(api_server: TestClient) -> None:
    """A username not in the users table returns 401."""
    resp = api_server.post(
        "/auth/login",
        data={"username": "ghost", "password": "test-pw", "grant_type": "password"},
    )
    assert resp.status_code == 401


def test_auth_me_returns_subject(api_server: TestClient) -> None:
    """GET /auth/me echoes the username embedded in the bearer token."""
    token = _login(api_server)
    resp = api_server.get("/auth/me", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json() == {"username": "default"}


def test_auth_me_requires_bearer(api_server: TestClient) -> None:
    resp = api_server.get("/auth/me")
    assert resp.status_code == 401


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
    uids = [p["uid"] for p in listing.json()["items"]]
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


def test_list_tasks_converts_date_query_params_to_datetime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Regression: ISO date query params must reach the CalDAV facade
    as ``datetime``, not ``str``.

    Without the conversion, caldav's server-side time-range filter
    raises ``AttributeError: 'str' object has no attribute
    'strftime'`` and the request 500s. Pinned by spying on
    ``list_tasks`` directly, since the mock backend ignores
    ``start``/``end`` and therefore would not catch this bug.
    """
    from datetime import datetime as _datetime

    from genesis.api.app import app as _app
    from genesis.api.routers import productivity as _router
    from genesis.users.store import UserStore

    captured: list[dict] = []

    class _SpyCalDAV:
        def list_tasks(self, **kwargs: object) -> list[object]:
            captured.append(kwargs)
            return []

    class _SpyFacade:
        def __init__(self) -> None:
            self.caldav = _SpyCalDAV()

        def shutdown(self) -> None:
            pass

    users_path = tmp_path / "users.yaml"
    UserStore(users_path).add("default", "test-pw")
    monkeypatch.setattr("genesis.users.store._default_store", UserStore(users_path))
    monkeypatch.setattr(_router, "_facade", lambda subject: _SpyFacade())

    with TestClient(_app) as client:
        token = client.post(
            "/auth/login",
            data={
                "username": "default",
                "password": "test-pw",
                "grant_type": "password",
            },
        ).json()["access_token"]

        resp = client.get(
            "/productivity/tasks?assigned_date_start=2026-09-01&assigned_date_end=2026-09-30",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert len(captured) == 1
    call = captured[0]
    assert isinstance(call["start"], _datetime)
    assert isinstance(call["end"], _datetime)
    assert call["start"].tzinfo is not None
    assert call["end"].tzinfo is not None


def test_list_tasks_rejects_malformed_date_query_param(
    api_server: TestClient,
) -> None:
    """Boundary check: malformed date input is rejected with 422,
    not silently passed through to crash the CalDAV facade.

    Pydantic parses the date query param on the API edge; bad
    input never reaches the facade.
    """
    token = _login(api_server)

    resp = api_server.get(
        "/productivity/tasks?assigned_date_start=not-a-date",
        headers=_auth(token),
    )
    assert resp.status_code == 422

    # Datetime fields reject date-only-ish input. ``hard_deadline_start``
    # expects the wire format ``YYYY-MM-DDTHH:MM:SS.mmmZ``; garbage
    # is a 422.
    resp = api_server.get(
        "/productivity/tasks?hard_deadline_start=garbage",
        headers=_auth(token),
    )
    assert resp.status_code == 422


def test_list_tasks_with_datetime_deadline_filter_does_not_500(
    api_server_with_mock: tuple[TestClient, MockCalDAVFacade],
) -> None:
    """Regression: querying by ``hard_deadline_*`` with a populated
    task must not raise ``TypeError: can't compare datetime.datetime
    to datetime.date``.

    The router's ``in_range`` helper used to coerce the task-side
    field to a ``date`` but pass the query-side datetime through
    unchanged, so the comparison crashed with a 500. The fix
    normalises both sides to ``date`` so calendar-day bounds apply
    uniformly regardless of which field is filtered.
    """
    from datetime import UTC, datetime

    from genesis.entities import TaskCreate

    client, mock = api_server_with_mock
    token = _login(client)
    headers = _auth(token)

    mock.create_task(
        TaskCreate(
            title="Ship the thing",
            hard_deadline=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
        )
    )

    resp = client.get(
        "/productivity/tasks?hard_deadline_start=2026-09-01T00:00:00.000Z&hard_deadline_end=2026-09-30T23:59:59.999Z",
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Ship the thing"


def test_list_tasks_passes_completed_at_params_to_facade(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The new ``completed_at_start`` / ``completed_at_end`` query
    params must reach the CalDAV facade as tz-aware ``datetime``
    objects. Mirrors the existing date-query-param regression test
    in style.
    """
    from datetime import datetime as _datetime

    from genesis.api.app import app as _app
    from genesis.api.routers import productivity as _router
    from genesis.users.store import UserStore

    captured: list[dict] = []

    class _SpyCalDAV:
        def list_tasks(self, **kwargs: object) -> list[object]:
            captured.append(kwargs)
            return []

    class _SpyFacade:
        def __init__(self) -> None:
            self.caldav = _SpyCalDAV()

        def shutdown(self) -> None:
            pass

    users_path = tmp_path / "users.yaml"
    UserStore(users_path).add("default", "test-pw")
    monkeypatch.setattr("genesis.users.store._default_store", UserStore(users_path))
    monkeypatch.setattr(_router, "_facade", lambda subject: _SpyFacade())

    with TestClient(_app) as client:
        token = client.post(
            "/auth/login",
            data={
                "username": "default",
                "password": "test-pw",
                "grant_type": "password",
            },
        ).json()["access_token"]

        resp = client.get(
            "/productivity/tasks"
            "?completed_at_start=2026-03-01T00:00:00.000Z"
            "&completed_at_end=2026-04-01T00:00:00.000Z",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert len(captured) == 1
    call = captured[0]
    assert isinstance(call["completed_at_start"], _datetime)
    assert isinstance(call["completed_at_end"], _datetime)
    assert call["completed_at_start"].tzinfo is not None
    assert call["completed_at_end"].tzinfo is not None


def test_list_tasks_filters_by_completed_at_range(
    api_server_with_mock: tuple[TestClient, MockCalDAVFacade],
) -> None:
    """End-to-end through the router: ``completed_at_start`` /
    ``completed_at_end`` filter to tasks with ``completed_at`` in
    window. Two in window, one outside, one uncompleted; only the
    two in window come back.
    """
    from datetime import UTC, datetime

    from genesis.entities import Task, TaskCreate, TaskStatus

    client, mock = api_server_with_mock
    token = _login(client)
    headers = _auth(token)

    def _seed(title: str, status: TaskStatus, completed_at: datetime | None) -> str:
        created = mock.create_task(TaskCreate(title=title))
        completed = Task(
            uid=created.uid,
            title=title,
            status=status,
            completed_at=completed_at,
            created=created.created,
            modified=created.modified,
        )
        mock._tasks[created.uid] = completed
        return created.uid

    in_one = _seed(
        "in-one",
        TaskStatus.COMPLETED,
        datetime(2026, 3, 10, 9, 0, tzinfo=UTC),
    )
    in_two = _seed(
        "in-two",
        TaskStatus.COMPLETED,
        datetime(2026, 3, 20, 14, 30, tzinfo=UTC),
    )
    _seed(
        "out",
        TaskStatus.COMPLETED,
        datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
    )
    _seed(
        "uncompleted",
        TaskStatus.NEEDS_ACTION,
        None,
    )

    resp = client.get(
        "/productivity/tasks"
        "?completed_at_start=2026-03-01T00:00:00.000Z"
        "&completed_at_end=2026-04-01T00:00:00.000Z",
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    uids = {item["uid"] for item in body["items"]}
    assert uids == {in_one, in_two}
    assert body["total"] == 2


def test_list_tasks_rejects_malformed_completed_at_param(
    api_server: TestClient,
) -> None:
    """Bad input is rejected at the API edge with 422, not passed
    through to crash the facade.
    """
    token = _login(api_server)
    resp = api_server.get(
        "/productivity/tasks?completed_at_start=not-a-date",
        headers=_auth(token),
    )
    assert resp.status_code == 422


@pytest.mark.integration
def test_list_tasks_filters_by_completed_at_round_trip(
    api_server_real: TestClient,
) -> None:
    """End-to-end against real Radicale: PATCH to COMPLETED writes
    the iCal COMPLETED property, and the new query params round-trip
    it back through the facade's filter.
    """

    token = _login(api_server_real)
    headers = _auth(token)

    def _create_and_complete(title: str) -> str:
        task = api_server_real.post(
            "/productivity/tasks",
            json={"title": title},
            headers=headers,
        ).json()
        # Stamp a known completed_at via the PATCH path by going
        # through update_task, then sleep-free backdating is not
        # possible from the wire. Use the wire "now" value the
        # server writes and query around it.
        api_server_real.patch(
            f"/productivity/tasks/{task['uid']}",
            json={"status": "completed"},
            headers=headers,
        )
        return task["uid"]

    in_uid = _create_and_complete("in")
    # The completion happens at wall-clock now; query a window that
    # surrounds it. We can't pick a deterministic date without a
    # wire-side override, so use a very wide window.
    resp = api_server_real.get(
        "/productivity/tasks"
        "?status=completed"
        "&completed_at_start=2020-01-01T00:00:00.000Z"
        "&completed_at_end=2099-01-01T00:00:00.000Z",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    uids = {item["uid"] for item in body["items"]}
    assert in_uid in uids

    # And a window that excludes "now" returns no rows for this task.
    resp_empty = api_server_real.get(
        "/productivity/tasks"
        "?completed_at_start=1970-01-01T00:00:00.000Z"
        "&completed_at_end=1970-01-02T00:00:00.000Z",
        headers=headers,
    )
    assert resp_empty.status_code == 200
    assert all(item["uid"] != in_uid for item in resp_empty.json()["items"])


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
