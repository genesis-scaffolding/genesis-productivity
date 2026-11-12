"""API contract tests for recurrent tasks (ADR-033).

Covers the wire contract of the recurrence endpoints: creating a
recurrent task, listing and reading the series, the series edit, the
ownership rule of an occurrence, the cancellation of a series, and
the delete of one occurrence.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from genesis.entities import TaskStatus


def _login(client: TestClient) -> str:
    resp = client.post(
        "/auth/login",
        data={"username": "default", "password": "test-pw", "grant_type": "password"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_recurrent(
    client: TestClient,
    token: str,
    *,
    count: int | None = None,
    title: str = "Pay rent",
    assigned_date: str = "2026-11-21",
    end_date: str | None = None,
) -> dict:
    recurrence: dict = {"frequency": "monthly", "interval": 1, "anchor": "assigned_date"}
    if count is not None:
        recurrence["materialized_future_count"] = count
    if end_date is not None:
        recurrence["end_date"] = end_date
    resp = client.post(
        "/productivity/tasks",
        json={"title": title, "assigned_date": assigned_date, "recurrence": recurrence},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_create_normal_task_is_unchanged(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.post("/productivity/tasks", json={"title": "Plain"}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Plain"
    assert body["recurrence"] is None


def test_create_recurrent_task_uses_the_global_default(api_server: TestClient) -> None:
    """Without an explicit count the per-user default (1) applies."""
    token = _login(api_server)
    body = _create_recurrent(api_server, token)
    recurrence = body["recurrence"]
    assert recurrence is not None
    assert recurrence["materialized_future_count"] == 1
    assert recurrence["frequency"] == "monthly"
    assert recurrence["interval"] == 1
    assert recurrence["anchor"] == "assigned_date"
    assert recurrence["recurrence_index"] == 0
    assert recurrence["occurrence_date"] == "2026-11-21"
    assert recurrence["start_date"] == "2026-11-21"
    assert recurrence["end_date"] is None
    assert recurrence["active"] is True
    assert recurrence["role"] == "current"


def test_create_recurrent_task_with_explicit_count(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=4)
    assert body["recurrence"]["materialized_future_count"] == 4

    series_uid = body["recurrence"]["series_uid"]
    listed = api_server.get("/productivity/tasks", params={"series_uid": series_uid}, headers=_auth(token))
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 5


def test_create_recurrent_task_with_an_end_date(api_server: TestClient) -> None:
    """The end date caps the materialized window."""
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=4, end_date="2026-12-21")
    recurrence = body["recurrence"]
    assert recurrence["end_date"] == "2026-12-21"

    series_uid = recurrence["series_uid"]
    listed = api_server.get("/productivity/tasks", params={"series_uid": series_uid}, headers=_auth(token))
    # The current occurrence and one future: the third one would fall
    # after the end date.
    assert listed.json()["total"] == 2


def test_create_recurrent_task_rejects_a_missing_anchor(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.post(
        "/productivity/tasks",
        json={"title": "No anchor", "recurrence": {"frequency": "monthly", "anchor": "assigned_date"}},
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text
    assert "assigned_date" in resp.json()["detail"]


def test_create_recurrent_task_rejects_a_count_below_one(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.post(
        "/productivity/tasks",
        json={
            "title": "Bad count",
            "assigned_date": "2026-11-21",
            "recurrence": {"frequency": "monthly", "anchor": "assigned_date", "materialized_future_count": 0},
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


def test_create_recurrent_task_rejects_a_count_above_the_cap(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.post(
        "/productivity/tasks",
        json={
            "title": "Too many",
            "assigned_date": "2026-11-21",
            "recurrence": {"frequency": "monthly", "anchor": "assigned_date", "materialized_future_count": 101},
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


def test_create_recurrent_task_rejects_an_end_date_before_the_start(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.post(
        "/productivity/tasks",
        json={
            "title": "Backwards",
            "assigned_date": "2026-11-21",
            "recurrence": {
                "frequency": "monthly",
                "anchor": "assigned_date",
                "end_date": "2026-01-01",
            },
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Series discovery
# ---------------------------------------------------------------------------


def test_list_series_reports_every_series(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=4)
    series_uid = body["recurrence"]["series_uid"]

    resp = api_server.get("/productivity/recurrences", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["total"] == 1
    series = payload["items"][0]
    assert series["uid"] == series_uid
    assert series["title"] == "Pay rent"
    assert series["frequency"] == "monthly"
    assert series["interval"] == 1
    assert series["anchor"] == "assigned_date"
    assert series["start_date"] == "2026-11-21"
    assert series["end_date"] is None
    assert series["materialized_future_count"] == 4
    assert series["status"] == "Active"
    assert series["current_task_uid"] == body["uid"]
    assert series["next_occurrence_date"] == "2026-12-21"
    assert series["open_occurrence_count"] == 5
    assert series["history_count"] == 0


def test_list_series_is_empty_without_a_series(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.get("/productivity/recurrences", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": [], "total": 0}


def test_read_series_reports_the_state(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=4)
    series_uid = body["recurrence"]["series_uid"]

    resp = api_server.get(f"/productivity/recurrences/{series_uid}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    series = resp.json()
    assert series["uid"] == series_uid
    assert series["materialized_future_count"] == 4
    assert series["active"] is True
    assert series["stop_reason"] is None
    assert series["current_task_uid"] == body["uid"]
    assert series["next_occurrence_date"] == "2026-12-21"
    assert len(series["future_occurrences"]) == 4
    assert [o["index"] for o in series["future_occurrences"]] == [1, 2, 3, 4]
    assert series["future_occurrences"][0]["occurrence_date"] == "2026-12-21"


def test_read_missing_series_is_404(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.get("/productivity/recurrences/r-missing", headers=_auth(token))
    assert resp.status_code == 404, resp.text


def test_list_by_series_uid_returns_current_futures_and_history(api_server: TestClient) -> None:
    """The series filter narrows the list to one series.

    Two series are seeded so the assertion proves the filter works
    and not merely that the fixture happens to hold one series' tasks.
    """
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2, title="Pay rent")
    series_uid = body["recurrence"]["series_uid"]
    other = _create_recurrent(api_server, token, count=1, title="Health insurance")
    other_series_uid = other["recurrence"]["series_uid"]
    assert other_series_uid != series_uid

    api_server.patch(
        f"/productivity/tasks/{body['uid']}",
        json={"status": "completed"},
        headers=_auth(token),
    )

    listed = api_server.get("/productivity/tasks", params={"series_uid": series_uid}, headers=_auth(token))
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    # The completed current occurrence, its two futures, and the tail
    # occurrence materialized when the current was completed.
    assert len(items) == 4
    assert {item["recurrence"]["series_uid"] for item in items} == {series_uid}
    assert any(item["status"] == "completed" for item in items)
    assert all(item["recurrence"]["series_uid"] == series_uid for item in items)

    # Without the filter both series are listed.
    unfiltered = api_server.get("/productivity/tasks", headers=_auth(token))
    assert unfiltered.status_code == 200, unfiltered.text
    assert unfiltered.json()["total"] == 6
    assert {item["recurrence"]["series_uid"] for item in unfiltered.json()["items"]} == {
        series_uid,
        other_series_uid,
    }


def test_list_enrichment_fetches_each_series_once(api_server_with_mock) -> None:
    """A list of occurrences of one series costs one series lookup."""
    client, mock = api_server_with_mock
    token = _login(client)
    _create_recurrent(client, token, count=4)

    calls = {"n": 0}
    original = mock.get_series

    def counting_get_series(uid):
        calls["n"] += 1
        return original(uid)

    mock.get_series = counting_get_series  # type: ignore[method-assign]
    resp = client.get("/productivity/tasks", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 5
    assert calls["n"] == 1


def test_the_role_of_an_occurrence_is_reported(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    series_uid = body["recurrence"]["series_uid"]

    listed = api_server.get("/productivity/tasks", params={"series_uid": series_uid}, headers=_auth(token))
    roles = sorted(item["recurrence"]["role"] for item in listed.json()["items"])
    assert roles == ["current", "future", "future"]


# ---------------------------------------------------------------------------
# Occurrence edits
# ---------------------------------------------------------------------------


def test_patch_without_a_scope_keeps_current_behaviour(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    resp = api_server.patch(
        f"/productivity/tasks/{body['uid']}",
        json={"title": "Only this one"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "Only this one"

    listed = api_server.get(
        "/productivity/tasks",
        params={"series_uid": body["recurrence"]["series_uid"]},
        headers=_auth(token),
    )
    titles = sorted(item["title"] for item in listed.json()["items"])
    assert titles == ["Only this one", "Pay rent", "Pay rent"]


def test_patch_rejects_a_series_owned_field(api_server: TestClient) -> None:
    """The schedule of an occurrence belongs to its series."""
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    resp = api_server.patch(
        f"/productivity/tasks/{body['uid']}",
        json={"assigned_date": "2026-12-01"},
        headers=_auth(token),
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert body["recurrence"]["series_uid"] in detail
    assert "assigned_date" in detail
    assert "/productivity/recurrences/" in detail


def test_patch_rejects_a_project_change_on_an_occurrence(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    resp = api_server.patch(
        f"/productivity/tasks/{body['uid']}",
        json={"project_id": "p-other"},
        headers=_auth(token),
    )
    assert resp.status_code == 409, resp.text
    assert "project_id" in resp.json()["detail"]


def test_patch_of_an_ordinary_task_keeps_its_dates(api_server: TestClient) -> None:
    token = _login(api_server)
    created = api_server.post(
        "/productivity/tasks",
        json={"title": "Plain", "assigned_date": "2026-11-21"},
        headers=_auth(token),
    )
    resp = api_server.patch(
        f"/productivity/tasks/{created.json()['uid']}",
        json={"assigned_date": "2026-12-01"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assigned_date"] == "2026-12-01"


def test_the_scope_fields_are_gone_from_the_contract(api_server: TestClient) -> None:
    """There is no edit scope on the wire any more."""
    schema = api_server.get("/openapi.json").json()
    task_out = schema["components"]["schemas"]["TaskOut"]["properties"]
    assert "scope" not in task_out
    assert "affected_uids" not in task_out
    task_in = schema["components"]["schemas"]["TaskUpdateIn"]["properties"]
    assert "scope" not in task_in
    assert "recurrence" not in task_in


def test_patch_missing_task_is_404(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.patch("/productivity/tasks/t-missing", json={"title": "x"}, headers=_auth(token))
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Series edit
# ---------------------------------------------------------------------------


def test_patch_series_applies_the_rule(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=4)
    series_uid = body["recurrence"]["series_uid"]

    resp = api_server.patch(
        f"/productivity/recurrences/{series_uid}",
        json={"interval": 2},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["series_uid"] == series_uid
    assert len(payload["updated_task_uids"]) == 5

    series = api_server.get(f"/productivity/recurrences/{series_uid}", headers=_auth(token))
    assert series.json()["interval"] == 2


def test_patch_series_applies_the_template(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    series_uid = body["recurrence"]["series_uid"]

    resp = api_server.patch(
        f"/productivity/recurrences/{series_uid}",
        json={"title": "Pay the rent", "priority": 8},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["updated_task_uids"]) == 3

    listed = api_server.get("/productivity/tasks", params={"series_uid": series_uid}, headers=_auth(token))
    assert {item["title"] for item in listed.json()["items"]} == {"Pay the rent"}


def test_patch_series_applies_the_dates(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    series_uid = body["recurrence"]["series_uid"]

    resp = api_server.patch(
        f"/productivity/recurrences/{series_uid}",
        json={"start_date": "2026-12-21", "end_date": "2027-05-21"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["end_date"] == "2027-05-21"

    series = api_server.get(f"/productivity/recurrences/{series_uid}", headers=_auth(token))
    assert series.json()["start_date"] == "2026-12-21"


def test_patch_series_applies_the_horizon(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=1)
    series_uid = body["recurrence"]["series_uid"]

    resp = api_server.patch(
        f"/productivity/recurrences/{series_uid}",
        json={"materialized_future_count": 4},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["materialized_future_count"] == 4
    assert len(payload["created_task_uids"]) == 3
    assert payload["canceled_task_uids"] == []


def test_patch_series_shrinking_the_horizon_cancels_the_farthest_rows(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=4)
    series_uid = body["recurrence"]["series_uid"]

    resp = api_server.patch(
        f"/productivity/recurrences/{series_uid}",
        json={"materialized_future_count": 1},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["materialized_future_count"] == 1
    assert len(payload["canceled_task_uids"]) == 3


def test_a_title_only_patch_leaves_a_finished_series_finished(api_server: TestClient) -> None:
    """A content-only update does not resurrect a finished series.

    Deleting every occurrence leaves the series with nothing to
    renew: it finishes. Only a schedule change brings it back.
    """
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    series_uid = body["recurrence"]["series_uid"]

    listed = api_server.get("/productivity/tasks", params={"series_uid": series_uid}, headers=_auth(token))
    for item in listed.json()["items"]:
        api_server.delete(f"/productivity/tasks/{item['uid']}", headers=_auth(token))

    resp = api_server.patch(
        f"/productivity/recurrences/{series_uid}",
        json={"title": "Pay the rent"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] is False

    series = api_server.get(f"/productivity/recurrences/{series_uid}", headers=_auth(token))
    assert series.json()["stop_reason"] == "finished"
    assert series.json()["active"] is False
    assert api_server.get("/productivity/tasks", params={"series_uid": series_uid}, headers=_auth(token)).json()[
        "total"
    ] == 0


def test_a_schedule_patch_reactivates_a_finished_series(api_server: TestClient) -> None:
    """A new end date that allows another occurrence brings it back."""
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    series_uid = body["recurrence"]["series_uid"]

    listed = api_server.get("/productivity/tasks", params={"series_uid": series_uid}, headers=_auth(token))
    for item in listed.json()["items"]:
        api_server.delete(f"/productivity/tasks/{item['uid']}", headers=_auth(token))

    resp = api_server.patch(
        f"/productivity/recurrences/{series_uid}",
        json={"end_date": "2027-05-21"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] is True
    assert resp.json()["end_date"] == "2027-05-21"

    series = api_server.get(f"/productivity/recurrences/{series_uid}", headers=_auth(token))
    assert series.json()["stop_reason"] is None
    assert api_server.get("/productivity/tasks", params={"series_uid": series_uid}, headers=_auth(token)).json()[
        "total"
    ] > 0


def test_patch_series_on_a_cancelled_series_is_409(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    series_uid = body["recurrence"]["series_uid"]

    cancelled = api_server.post(f"/productivity/recurrences/{series_uid}/cancel", headers=_auth(token))
    assert cancelled.status_code == 200, cancelled.text

    resp = api_server.patch(
        f"/productivity/recurrences/{series_uid}",
        json={"interval": 2},
        headers=_auth(token),
    )
    assert resp.status_code == 409, resp.text


def test_patch_series_with_an_end_date_before_the_start_is_422(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    series_uid = body["recurrence"]["series_uid"]

    resp = api_server.patch(
        f"/productivity/recurrences/{series_uid}",
        json={"end_date": "2020-01-01"},
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


def test_patch_missing_series_is_404(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.patch(
        "/productivity/recurrences/r-missing",
        json={"interval": 2},
        headers=_auth(token),
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_series_reports_every_cancelled_uid(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=4)

    resp = api_server.post(
        f"/productivity/recurrences/{body['recurrence']['series_uid']}/cancel",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["series_uid"] == body["recurrence"]["series_uid"]
    assert len(payload["canceled_uids"]) == 4
    assert payload["active"] is False


def test_cancel_series_keeps_the_current_occurrence(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    api_server.post(f"/productivity/recurrences/{body['recurrence']['series_uid']}/cancel", headers=_auth(token))

    read = api_server.get(f"/productivity/tasks/{body['uid']}", headers=_auth(token))
    assert read.status_code == 200, read.text
    assert read.json()["status"] == "needs-action"


def test_cancel_series_is_idempotent(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    series_uid = body["recurrence"]["series_uid"]

    first = api_server.post(f"/productivity/recurrences/{series_uid}/cancel", headers=_auth(token))
    second = api_server.post(f"/productivity/recurrences/{series_uid}/cancel", headers=_auth(token))
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert len(first.json()["canceled_uids"]) == 2
    assert second.json()["canceled_uids"] == []


def test_cancel_missing_series_is_404(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.post("/productivity/recurrences/r-missing/cancel", headers=_auth(token))
    assert resp.status_code == 404, resp.text


def test_the_series_status_is_reported(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    series_uid = body["recurrence"]["series_uid"]
    api_server.post(f"/productivity/recurrences/{series_uid}/cancel", headers=_auth(token))

    listed = api_server.get("/productivity/recurrences", headers=_auth(token))
    series = listed.json()["items"][0]
    assert series["status"] == "Cancelled"
    assert series["active"] is False
    assert series["stop_reason"] == "cancelled"


# ---------------------------------------------------------------------------
# Deleting an occurrence
# ---------------------------------------------------------------------------


def test_delete_an_occurrence_keeps_the_series_running(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=4)
    series_uid = body["recurrence"]["series_uid"]

    listed = api_server.get("/productivity/tasks", params={"series_uid": series_uid}, headers=_auth(token))
    future = next(item for item in listed.json()["items"] if item["recurrence"]["role"] == "future")

    resp = api_server.delete(f"/productivity/tasks/{future['uid']}", headers=_auth(token))
    assert resp.status_code == 204, resp.text

    # The deleted occurrence does not come back.
    listed = api_server.get("/productivity/tasks", params={"series_uid": series_uid}, headers=_auth(token))
    assert future["uid"] not in {item["uid"] for item in listed.json()["items"]}

    series = api_server.get(f"/productivity/recurrences/{series_uid}", headers=_auth(token))
    assert series.json()["active"] is True


def test_delete_with_cancel_series_cancels_the_series(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=4)

    resp = api_server.delete(
        f"/productivity/tasks/{body['uid']}",
        params={"cancel_series": "true"},
        headers=_auth(token),
    )
    assert resp.status_code == 204, resp.text

    listed = api_server.get("/productivity/recurrences", headers=_auth(token))
    series = listed.json()["items"][0]
    assert series["status"] == "Cancelled"
    assert series["active"] is False


def test_delete_missing_task_is_404(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.delete("/productivity/tasks/t-missing", headers=_auth(token))
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Compatibility
# ---------------------------------------------------------------------------


def test_get_endpoints_have_no_request_body(api_server: TestClient) -> None:
    """The existing GET endpoints keep their body-free contract."""
    schema = api_server.get("/openapi.json").json()
    for path in ("/productivity/tasks", "/productivity/tasks/{uid}", "/productivity/recurrences/{series_uid}"):
        operation = next(iter(schema["paths"][path].values()))
        assert "requestBody" not in operation, path


def test_task_status_values_are_unchanged(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=1)
    resp = api_server.patch(
        f"/productivity/tasks/{body['uid']}",
        json={"status": "completed"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"
    assert TaskStatus.COMPLETED.value == "completed"


def test_read_series_includes_the_template(api_server: TestClient) -> None:
    """The series inspection reports the future template."""
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=4)
    series_uid = body["recurrence"]["series_uid"]

    resp = api_server.get(f"/productivity/recurrences/{series_uid}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    template = resp.json()["template"]
    assert template["title"] == "Pay rent"
    assert template["assigned_offset_days"] == 0
    assert template["deadline_offset_days"] is None
    assert template["scheduled_offset_days"] is None
    assert template["project_id"] is None
    assert template["priority"] is None
