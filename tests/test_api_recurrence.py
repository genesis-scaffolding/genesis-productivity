"""API contract tests for recurrent tasks (ADR-033).

Covers the wire contract of the recurrence endpoints: creating a
recurrent task, reading a series, listing by series, the two edit
scopes, changing the materialized horizon, and cancelling the future.
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
) -> dict:
    recurrence: dict = {"frequency": "monthly", "interval": 1, "anchor": "assigned_date"}
    if count is not None:
        recurrence["materialized_future_count"] = count
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
    assert body["scope"] is None
    assert body["affected_uids"] == []


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
    assert recurrence["active"] is True


def test_create_recurrent_task_with_explicit_count(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=4)
    assert body["recurrence"]["materialized_future_count"] == 4

    series_uid = body["recurrence"]["series_uid"]
    listed = api_server.get("/productivity/tasks", params={"series_uid": series_uid}, headers=_auth(token))
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 5


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


# ---------------------------------------------------------------------------
# Series inspection
# ---------------------------------------------------------------------------


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
    assert series["current_task_uid"] == body["uid"]
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


# ---------------------------------------------------------------------------
# Edit scopes
# ---------------------------------------------------------------------------


def test_patch_without_scope_keeps_current_behaviour(api_server: TestClient) -> None:
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


def test_patch_with_current_scope_changes_only_the_occurrence(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    resp = api_server.patch(
        f"/productivity/tasks/{body['uid']}",
        json={"title": "Only this one", "scope": "current"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["affected_uids"] == [body["uid"]]


def test_patch_with_all_future_scope_reports_every_affected_uid(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=4)
    resp = api_server.patch(
        f"/productivity/tasks/{body['uid']}",
        json={"title": "All of them", "scope": "all-future"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["scope"] == "all-future"
    assert len(payload["affected_uids"]) == 4
    assert body["uid"] not in payload["affected_uids"]


def test_patch_with_all_future_scope_and_status_is_rejected(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    resp = api_server.patch(
        f"/productivity/tasks/{body['uid']}",
        json={"status": "completed", "scope": "all-future"},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.text
    assert "status" in resp.json()["detail"]


def test_patch_with_a_scope_on_a_plain_task_is_rejected(api_server: TestClient) -> None:
    token = _login(api_server)
    created = api_server.post("/productivity/tasks", json={"title": "Plain"}, headers=_auth(token))
    resp = api_server.patch(
        f"/productivity/tasks/{created.json()['uid']}",
        json={"title": "Nope", "scope": "all-future"},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.text


def test_patch_missing_task_is_404(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.patch("/productivity/tasks/t-missing", json={"title": "x"}, headers=_auth(token))
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Horizon
# ---------------------------------------------------------------------------


def test_patch_series_horizon_up_creates_the_missing_rows(api_server: TestClient) -> None:
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
    assert len(payload["future_task_uids"]) == 4
    assert payload["canceled_task_uids"] == []


def test_patch_series_horizon_down_cancels_the_farthest_rows(api_server: TestClient) -> None:
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
    assert len(payload["future_task_uids"]) == 1


def test_patch_series_horizon_to_the_same_value_is_a_noop(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    series_uid = body["recurrence"]["series_uid"]

    resp = api_server.patch(
        f"/productivity/recurrences/{series_uid}",
        json={"materialized_future_count": 2},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["created_task_uids"] == []
    assert payload["canceled_task_uids"] == []


def test_patch_series_horizon_on_an_inactive_series_is_409(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    series_uid = body["recurrence"]["series_uid"]

    cancelled = api_server.post(f"/productivity/tasks/{body['uid']}/cancel-future", headers=_auth(token))
    assert cancelled.status_code == 200, cancelled.text

    resp = api_server.patch(
        f"/productivity/recurrences/{series_uid}",
        json={"materialized_future_count": 4},
        headers=_auth(token),
    )
    assert resp.status_code == 409, resp.text


def test_patch_missing_series_is_404(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.patch(
        "/productivity/recurrences/r-missing",
        json={"materialized_future_count": 2},
        headers=_auth(token),
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_future_reports_every_cancelled_uid(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=4)

    resp = api_server.post(f"/productivity/tasks/{body['uid']}/cancel-future", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["series_uid"] == body["recurrence"]["series_uid"]
    assert len(payload["canceled_uids"]) == 4
    assert payload["active"] is False


def test_cancel_future_keeps_the_current_occurrence(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    api_server.post(f"/productivity/tasks/{body['uid']}/cancel-future", headers=_auth(token))

    read = api_server.get(f"/productivity/tasks/{body['uid']}", headers=_auth(token))
    assert read.status_code == 200, read.text
    assert read.json()["status"] == "needs-action"


def test_cancel_future_on_a_plain_task_is_rejected(api_server: TestClient) -> None:
    token = _login(api_server)
    created = api_server.post("/productivity/tasks", json={"title": "Plain"}, headers=_auth(token))
    resp = api_server.post(f"/productivity/tasks/{created.json()['uid']}/cancel-future", headers=_auth(token))
    assert resp.status_code == 400, resp.text


def test_cancel_future_on_a_missing_task_is_404(api_server: TestClient) -> None:
    token = _login(api_server)
    resp = api_server.post("/productivity/tasks/t-missing/cancel-future", headers=_auth(token))
    assert resp.status_code == 404, resp.text


def test_cancel_future_is_idempotent(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)

    first = api_server.post(f"/productivity/tasks/{body['uid']}/cancel-future", headers=_auth(token))
    second = api_server.post(f"/productivity/tasks/{body['uid']}/cancel-future", headers=_auth(token))
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert len(first.json()["canceled_uids"]) == 2
    assert second.json()["canceled_uids"] == []


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


def test_patch_scope_defaults_to_current_in_the_response(api_server: TestClient) -> None:
    """The response reports the applied scope, not the requested one."""
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    resp = api_server.patch(
        f"/productivity/tasks/{body['uid']}",
        json={"title": "Renamed"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "current"
    assert resp.json()["affected_uids"] == [body["uid"]]


def test_patch_with_an_explicit_current_scope_reports_current(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    resp = api_server.patch(
        f"/productivity/tasks/{body['uid']}",
        json={"title": "Renamed", "scope": "current"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "current"


def test_patch_with_an_explicit_all_future_scope_reports_all_future(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    resp = api_server.patch(
        f"/productivity/tasks/{body['uid']}",
        json={"title": "Renamed", "scope": "all-future"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "all-future"


def test_patch_with_a_rule_change_and_the_current_scope_is_rejected(api_server: TestClient) -> None:
    """A rule change needs the all-future scope.

    Answering 200 for a request that changed nothing would hide the
    mistake from the caller.
    """
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    resp = api_server.patch(
        f"/productivity/tasks/{body['uid']}",
        json={"recurrence": {"frequency": "weekly"}},
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.text
    assert "all-future" in resp.json()["detail"]

    # The rule is unchanged.
    series = api_server.get(f"/productivity/recurrences/{body['recurrence']['series_uid']}", headers=_auth(token))
    assert series.status_code == 200, series.text
    assert series.json()["frequency"] == "monthly"


def test_patch_with_a_rule_change_and_the_all_future_scope_is_applied(api_server: TestClient) -> None:
    token = _login(api_server)
    body = _create_recurrent(api_server, token, count=2)
    resp = api_server.patch(
        f"/productivity/tasks/{body['uid']}",
        json={"recurrence": {"frequency": "weekly"}, "scope": "all-future"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "all-future"

    series = api_server.get(f"/productivity/recurrences/{body['recurrence']['series_uid']}", headers=_auth(token))
    assert series.status_code == 200, series.text
    assert series.json()["frequency"] == "weekly"
