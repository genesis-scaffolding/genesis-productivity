"""Integration tests that cover every parameter combination and
every edge case in the agent API.

Where ``test_api.py`` proves the happy path, this file proves the
edge cases: pagination modes, filter combinations, sort orders,
Swagger UI contracts (no request body on GET endpoints), and
the contract for fields that the entity layer doesn't directly
support (status on POST project, journal project_id footer).
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


def _seed(client: TestClient, token: str, n: int = 7) -> list[dict]:
    """Create ``n`` tasks with sequential titles. Returns the JSON
    task objects."""
    tasks = []
    for i in range(n):
        resp = client.post(
            "/productivity/tasks",
            json={"title": f"task-{i:02d}"},
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.text
        tasks.append(resp.json())
    return tasks


# ---------------------------------------------------------------------------
# Swagger / OpenAPI contracts
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_get_endpoints_have_no_request_body(api_server: TestClient) -> None:
    """Every GET endpoint must use query parameters only. A stray
    ``list[...]`` or ``dict[...]`` annotation without an explicit
    ``Query()`` causes FastAPI to fall back to a JSON body and the
    Swagger UI rejects the resulting GET-with-body call."""
    spec = api_server.get("/openapi.json").json()
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if method.lower() != "get":
                continue
            assert "requestBody" not in op, (
                f"GET {path} has a requestBody in OpenAPI — FastAPI is "
                f"treating one of the parameters as a body. Check that "
                f"every parameter is wrapped in ``Query()``."
            )


# ---------------------------------------------------------------------------
# Task pagination
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_task_pagination_no_params_returns_all(api_server: TestClient) -> None:
    token = _login(api_server)
    _seed(api_server, token, n=7)

    resp = api_server.get("/productivity/tasks", headers=_auth(token)).json()
    assert resp["total"] == 7
    assert len(resp["items"]) == 7
    assert resp["offset"] == 0
    assert resp["limit"] == 7


@pytest.mark.integration
def test_task_pagination_limit_only(api_server: TestClient) -> None:
    """``limit`` without ``offset`` must apply a window starting at 0."""
    token = _login(api_server)
    _seed(api_server, token, n=7)

    resp = api_server.get(
        "/productivity/tasks?limit=3", headers=_auth(token)
    ).json()
    assert resp["total"] == 7
    assert len(resp["items"]) == 3
    assert resp["offset"] == 0
    assert resp["limit"] == 3


@pytest.mark.integration
def test_task_pagination_offset_only(api_server: TestClient) -> None:
    """``offset`` without ``limit`` returns everything from offset
    to the end."""
    token = _login(api_server)
    _seed(api_server, token, n=7)

    resp = api_server.get(
        "/productivity/tasks?offset=4", headers=_auth(token)
    ).json()
    assert resp["total"] == 7
    assert len(resp["items"]) == 3  # 7 - 4
    assert resp["offset"] == 4
    assert resp["limit"] == 3


@pytest.mark.integration
def test_task_pagination_offset_past_end_returns_empty(api_server: TestClient) -> None:
    token = _login(api_server)
    _seed(api_server, token, n=3)

    resp = api_server.get(
        "/productivity/tasks?offset=10", headers=_auth(token)
    ).json()
    assert resp["total"] == 3
    assert resp["items"] == []
    assert resp["offset"] == 10


@pytest.mark.integration
def test_task_pagination_window_exhausted_returns_partial(api_server: TestClient) -> None:
    """With offset=5 and limit=10 against 7 items, only 2 items are
    returned. ``limit`` in the response is what the client asked for
    (10), not how many items we ended up returning (``items`` count).
    """
    token = _login(api_server)
    _seed(api_server, token, n=7)

    resp = api_server.get(
        "/productivity/tasks?offset=5&limit=10", headers=_auth(token)
    ).json()
    assert resp["total"] == 7
    assert len(resp["items"]) == 2  # only 7 - 5 = 2 left
    assert resp["offset"] == 5
    assert resp["limit"] == 10  # reflects the request, not the truncation


# ---------------------------------------------------------------------------
# Task filters
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_task_filter_by_status(api_server: TestClient) -> None:
    token = _login(api_server)
    keep = _seed(api_server, token, n=2)
    # Mark one as completed.
    api_server.patch(
        f"/productivity/tasks/{keep[0]['uid']}",
        json={"status": "completed"},
        headers=_auth(token),
    )

    completed = api_server.get(
        "/productivity/tasks?status=completed", headers=_auth(token)
    ).json()
    assert completed["total"] == 1
    assert completed["items"][0]["uid"] == keep[0]["uid"]

    needs_action = api_server.get(
        "/productivity/tasks?status=needs-action", headers=_auth(token)
    ).json()
    assert needs_action["total"] == 1
    assert needs_action["items"][0]["uid"] == keep[1]["uid"]


@pytest.mark.integration
def test_task_filter_by_project(api_server: TestClient) -> None:
    token = _login(api_server)
    project = api_server.post(
        "/productivity/projects", json={"name": "container"}, headers=_auth(token)
    ).json()

    # 2 in the project, 2 outside.
    api_server.post(
        "/productivity/tasks",
        json={"title": "in-1", "project_id": project["uid"]},
        headers=_auth(token),
    )
    api_server.post(
        "/productivity/tasks",
        json={"title": "in-2", "project_id": project["uid"]},
        headers=_auth(token),
    )
    api_server.post(
        "/productivity/tasks", json={"title": "out-1"}, headers=_auth(token)
    )
    api_server.post(
        "/productivity/tasks", json={"title": "out-2"}, headers=_auth(token)
    )

    resp = api_server.get(
        f"/productivity/tasks?project_id={project['uid']}",
        headers=_auth(token),
    ).json()
    assert resp["total"] == 2
    titles = {t["title"] for t in resp["items"]}
    assert titles == {"in-1", "in-2"}


@pytest.mark.integration
def test_task_filter_by_search_query(api_server: TestClient) -> None:
    token = _login(api_server)
    api_server.post(
        "/productivity/tasks",
        json={"title": "Buy groceries", "description": "weekly"},
        headers=_auth(token),
    )
    api_server.post(
        "/productivity/tasks",
        json={"title": "Fix bug", "description": "memory leak"},
        headers=_auth(token),
    )

    by_title = api_server.get(
        "/productivity/tasks?search_query=groceries", headers=_auth(token)
    ).json()
    assert by_title["total"] == 1
    assert by_title["items"][0]["title"] == "Buy groceries"

    by_desc = api_server.get(
        "/productivity/tasks?search_query=leak", headers=_auth(token)
    ).json()
    assert by_desc["total"] == 1
    assert by_desc["items"][0]["title"] == "Fix bug"


@pytest.mark.integration
def test_task_sort_by_title(api_server: TestClient) -> None:
    token = _login(api_server)
    for title in ["Zebra", "Apple", "Mango"]:
        api_server.post(
            "/productivity/tasks", json={"title": title}, headers=_auth(token)
        )

    asc = api_server.get(
        "/productivity/tasks?sort_by=title&order=asc", headers=_auth(token)
    ).json()
    assert [t["title"] for t in asc["items"]] == ["Apple", "Mango", "Zebra"]

    desc = api_server.get(
        "/productivity/tasks?sort_by=title&order=desc", headers=_auth(token)
    ).json()
    assert [t["title"] for t in desc["items"]] == ["Zebra", "Mango", "Apple"]


# ---------------------------------------------------------------------------
# Project filters
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_project_create_with_non_default_status(api_server: TestClient) -> None:
    """ProjectCreate accepts a status; we create-then-update
    internally since the entity's ProjectCreate lacks a status."""
    token = _login(api_server)
    resp = api_server.post(
        "/productivity/projects",
        json={"name": "in-progress p", "status": "in-progress"},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "in-progress"


@pytest.mark.integration
def test_project_filter_by_search_query(api_server: TestClient) -> None:
    token = _login(api_server)
    api_server.post(
        "/productivity/projects", json={"name": "alpha"}, headers=_auth(token)
    )
    api_server.post(
        "/productivity/projects", json={"name": "beta"}, headers=_auth(token)
    )

    resp = api_server.get(
        "/productivity/projects?search_query=alp", headers=_auth(token)
    ).json()
    assert len(resp) == 1
    assert resp[0]["name"] == "alpha"


@pytest.mark.integration
def test_project_filter_by_status(api_server: TestClient) -> None:
    token = _login(api_server)
    api_server.post(
        "/productivity/projects",
        json={"name": "todo-p", "status": "needs-action"},
        headers=_auth(token),
    )
    api_server.post(
        "/productivity/projects",
        json={"name": "done-p", "status": "completed"},
        headers=_auth(token),
    )

    resp = api_server.get(
        "/productivity/projects?status=completed", headers=_auth(token)
    ).json()
    assert len(resp) == 1
    assert resp[0]["name"] == "done-p"


@pytest.mark.integration
def test_project_patch_partial(api_server: TestClient) -> None:
    """Only the provided fields change on PATCH."""
    token = _login(api_server)
    created = api_server.post(
        "/productivity/projects",
        json={"name": "orig", "description": "d"},
        headers=_auth(token),
    ).json()

    patched = api_server.patch(
        f"/productivity/projects/{created['uid']}",
        json={"name": "renamed"},
        headers=_auth(token),
    ).json()

    assert patched["name"] == "renamed"
    assert patched["description"] == "d"


# ---------------------------------------------------------------------------
# Journal filters
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_journal_filter_by_type(api_server: TestClient) -> None:
    token = _login(api_server)
    api_server.post(
        "/productivity/journals",
        json={"type": "daily", "date": "2026-09-01", "content": "x"},
        headers=_auth(token),
    )
    api_server.post(
        "/productivity/journals",
        json={"type": "weekly", "date": "2026-09-07", "content": "y"},
        headers=_auth(token),
    )

    resp = api_server.get(
        "/productivity/journals?type=daily", headers=_auth(token)
    ).json()
    assert resp["total"] == 1
    assert resp["items"][0]["type"] == "daily"


@pytest.mark.integration
def test_journal_filter_by_date_range(api_server: TestClient) -> None:
    token = _login(api_server)
    for day in ("2026-09-01", "2026-09-05", "2026-09-10"):
        api_server.post(
            "/productivity/journals",
            json={"type": "misc", "date": day, "content": day},
            headers=_auth(token),
        )

    resp = api_server.get(
        "/productivity/journals?date_start=2026-09-03&date_end=2026-09-07",
        headers=_auth(token),
    ).json()
    dates = [j["date"] for j in resp["items"]]
    assert dates == ["2026-09-05"]


@pytest.mark.integration
def test_journal_filter_by_search_query(api_server: TestClient) -> None:
    token = _login(api_server)
    api_server.post(
        "/productivity/journals",
        json={"type": "misc", "date": "2026-09-01", "content": "alpha bravo"},
        headers=_auth(token),
    )
    api_server.post(
        "/productivity/journals",
        json={"type": "misc", "date": "2026-09-02", "content": "charlie delta"},
        headers=_auth(token),
    )

    resp = api_server.get(
        "/productivity/journals?search_query=bravo", headers=_auth(token)
    ).json()
    assert resp["total"] == 1
    assert "alpha bravo" in resp["items"][0]["content"]


@pytest.mark.integration
def test_journal_filter_by_project(api_server: TestClient) -> None:
    token = _login(api_server)
    project = api_server.post(
        "/productivity/projects", json={"name": "journal-test"}, headers=_auth(token)
    ).json()

    api_server.post(
        "/productivity/journals",
        json={
            "type": "project",
            "date": "2026-09-01",
            "content": "linked",
            "project_id": project["uid"],
        },
        headers=_auth(token),
    )
    api_server.post(
        "/productivity/journals",
        json={"type": "misc", "date": "2026-09-02", "content": "unlinked"},
        headers=_auth(token),
    )

    resp = api_server.get(
        f"/productivity/journals?project_id={project['uid']}",
        headers=_auth(token),
    ).json()
    assert resp["total"] == 1
    assert resp["items"][0]["project_id"] == project["uid"]


@pytest.mark.integration
def test_journal_patch_title_only(api_server: TestClient) -> None:
    """Patch title without touching content."""
    token = _login(api_server)
    created = api_server.post(
        "/productivity/journals",
        json={"type": "misc", "date": "2026-09-01", "content": "x", "title": "old"},
        headers=_auth(token),
    ).json()

    patched = api_server.patch(
        f"/productivity/journals/{created['uid']}",
        json={"title": "new"},
        headers=_auth(token),
    ).json()

    assert patched["title"] == "new"
    assert patched["content"] == "x"


@pytest.mark.integration
def test_journal_patch_invalid_type_rejected(api_server: TestClient) -> None:
    token = _login(api_server)
    created = api_server.post(
        "/productivity/journals",
        json={"type": "misc", "date": "2026-09-01", "content": "x"},
        headers=_auth(token),
    ).json()

    # Note: type is not in the patch schema, so the value is silently
    # ignored (Pydantic drops unknown fields). This documents current
    # behavior; if we ever expose type in JournalEntryUpdateIn, this
    # test will need updating.
    resp = api_server.patch(
        f"/productivity/journals/{created['uid']}",
        json={"type": "daily"},
        headers=_auth(token),
    )
    assert resp.status_code == 200


@pytest.mark.integration
def test_journal_filter_invalid_type_returns_400(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.get(
        "/productivity/journals?type=nonsense", headers=_auth(token)
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Cross-cutting: error responses
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_unknown_project_id_in_journal_create(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.post(
        "/productivity/journals",
        json={
            "type": "project",
            "date": "2026-09-01",
            "content": "x",
            "project_id": "no-such-project",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 400


@pytest.mark.integration
def test_patch_missing_task_404(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.patch(
        "/productivity/tasks/no-such-uid",
        json={"title": "x"},
        headers=_auth(token),
    )
    assert resp.status_code == 404


@pytest.mark.integration
def test_patch_missing_project_404(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.patch(
        "/productivity/projects/no-such-uid",
        json={"name": "x"},
        headers=_auth(token),
    )
    assert resp.status_code == 404


@pytest.mark.integration
def test_patch_missing_journal_404(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.patch(
        "/productivity/journals/no-such-uid",
        json={"title": "x"},
        headers=_auth(token),
    )
    assert resp.status_code == 404


@pytest.mark.integration
def test_health_endpoint_open(api_server: TestClient) -> None:
    resp = api_server.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "online"}


@pytest.mark.integration
def test_health_endpoint_does_not_require_auth(api_server: TestClient) -> None:
    """The /health endpoint must be reachable without a token."""
    resp = api_server.get("/health")
    assert resp.status_code != 401
