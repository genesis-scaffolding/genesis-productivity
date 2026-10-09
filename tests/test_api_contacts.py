"""Unit tests for the contact API endpoints.

Drives the FastAPI :class:`TestClient` through the standard
``api_server`` fixture (mock-backed, no real Radicale). Covers
the public contract only: auth, pagination, CRUD, search, and
the wire-schema field set.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def authed_client(api_server: TestClient) -> Generator[TestClient, None, None]:
    """Yield a TestClient with a valid bearer token.

    The :func:`api_server` fixture already mounts the mock
    facade, so this only deals with the auth handshake.
    """
    response = api_server.post(
        "/auth/login",
        data={"username": "default", "password": "test-pw", "grant_type": "password"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    api_server.headers["Authorization"] = f"Bearer {token}"
    yield api_server


# =============================================================================
# Auth
# =============================================================================


class TestContactAuth:
    def test_list_requires_auth(self, api_server: TestClient) -> None:
        # Wipe the Authorization header the fixture sets.
        api_server.headers.pop("Authorization", None)
        response = api_server.get("/productivity/contacts")
        assert response.status_code == 401

    def test_create_requires_auth(self, api_server: TestClient) -> None:
        api_server.headers.pop("Authorization", None)
        response = api_server.post("/productivity/contacts", json={"name": "Anon"})
        assert response.status_code == 401


# =============================================================================
# CRUD
# =============================================================================


class TestContactCRUD:
    def test_create_returns_201_with_contact(self, authed_client: TestClient) -> None:
        response = authed_client.post(
            "/productivity/contacts",
            json={"name": "Ada Lovelace", "emails": ["ada@example.com"]},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Ada Lovelace"
        assert body["emails"] == ["ada@example.com"]
        assert body["uid"].startswith("c-")
        assert body["created"] is not None

    def test_create_with_minimal_payload(self, authed_client: TestClient) -> None:
        response = authed_client.post("/productivity/contacts", json={"name": "Bare"})
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Bare"
        assert body["emails"] == []
        assert body["phones"] == []
        assert body["urls"] == []

    def test_create_with_full_payload(self, authed_client: TestClient) -> None:
        response = authed_client.post(
            "/productivity/contacts",
            json={
                "name": "Ada Lovelace",
                "given_name": "Ada",
                "family_name": "Lovelace",
                "additional_names": ["M"],
                "name_prefix": "Dr.",
                "name_suffix": "Jr.",
                "nickname": "The Enchantress",
                "organization": "Analytical Engine Co",
                "title": "Programmer",
                "emails": ["ada@example.com", "ada@math.org"],
                "phones": ["+44 20 7946 0958"],
                "urls": ["https://example.com/ada"],
                "addresses": ["10 St James Square, London"],
                "note": "First programmer",
                "birthday": "1815-12-10",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["given_name"] == "Ada"
        assert body["family_name"] == "Lovelace"
        assert body["additional_names"] == ["M"]
        assert body["emails"] == ["ada@example.com", "ada@math.org"]
        assert body["birthday"] == "1815-12-10"

    def test_get_returns_stored_contact(self, authed_client: TestClient) -> None:
        created = authed_client.post("/productivity/contacts", json={"name": "Ada"}).json()
        response = authed_client.get(f"/productivity/contacts/{created['uid']}")
        assert response.status_code == 200
        assert response.json()["name"] == "Ada"

    def test_get_missing_returns_404(self, authed_client: TestClient) -> None:
        response = authed_client.get("/productivity/contacts/c-missing")
        assert response.status_code == 404

    def test_update_modifies_contact(self, authed_client: TestClient) -> None:
        created = authed_client.post("/productivity/contacts", json={"name": "Ada"}).json()
        response = authed_client.patch(
            f"/productivity/contacts/{created['uid']}",
            json={"name": "Augusta Ada", "nickname": "The Enchantress"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Augusta Ada"
        assert body["nickname"] == "The Enchantress"

    def test_update_can_clear_optional_field(self, authed_client: TestClient) -> None:
        created = authed_client.post(
            "/productivity/contacts",
            json={"name": "Ada", "organization": "ACME"},
        ).json()
        response = authed_client.patch(
            f"/productivity/contacts/{created['uid']}",
            json={"clear_organization": True},
        )
        assert response.status_code == 200
        assert response.json()["organization"] is None

    def test_update_missing_returns_404(self, authed_client: TestClient) -> None:
        response = authed_client.patch("/productivity/contacts/c-missing", json={"name": "X"})
        assert response.status_code == 404

    def test_delete_returns_204(self, authed_client: TestClient) -> None:
        created = authed_client.post("/productivity/contacts", json={"name": "Disposable"}).json()
        response = authed_client.delete(f"/productivity/contacts/{created['uid']}")
        assert response.status_code == 204
        # Subsequent GET returns 404.
        assert authed_client.get(f"/productivity/contacts/{created['uid']}").status_code == 404

    def test_delete_missing_returns_404(self, authed_client: TestClient) -> None:
        response = authed_client.delete("/productivity/contacts/c-missing")
        assert response.status_code == 404


# =============================================================================
# List, search, sort, pagination
# =============================================================================


class TestListAndSearch:
    def test_list_returns_paginated_envelope(self, authed_client: TestClient) -> None:
        for i in range(3):
            authed_client.post("/productivity/contacts", json={"name": f"Contact {i}"})
        response = authed_client.get("/productivity/contacts")
        assert response.status_code == 200
        body = response.json()
        assert "items" in body
        assert "total" in body
        assert "offset" in body
        assert "limit" in body
        assert body["total"] == 3
        assert len(body["items"]) == 3

    def test_list_pagination(self, authed_client: TestClient) -> None:
        for i in range(5):
            authed_client.post("/productivity/contacts", json={"name": f"C{i}"})
        response = authed_client.get("/productivity/contacts?limit=2&offset=1")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 5
        assert body["offset"] == 1
        assert body["limit"] == 2
        assert len(body["items"]) == 2

    def test_search_filters_by_query(self, authed_client: TestClient) -> None:
        authed_client.post("/productivity/contacts", json={"name": "Ada Lovelace"})
        authed_client.post("/productivity/contacts", json={"name": "Alice Cooper"})
        authed_client.post("/productivity/contacts", json={"name": "Bob Brown"})
        response = authed_client.get("/productivity/contacts?q=ada")
        body = response.json()
        # "ada" matches "Ada Lovelace" (name substring) but not
        # "Alice Cooper" or "Bob Brown" (no "ada" anywhere in
        # name, org, nickname, or emails).
        names = {c["name"] for c in body["items"]}
        assert names == {"Ada Lovelace"}

    def test_sort_by_name(self, authed_client: TestClient) -> None:
        authed_client.post("/productivity/contacts", json={"name": "Charlie"})
        authed_client.post("/productivity/contacts", json={"name": "Alice"})
        authed_client.post("/productivity/contacts", json={"name": "Bob"})
        response = authed_client.get("/productivity/contacts?sort_by=name&order=asc")
        names = [c["name"] for c in response.json()["items"]]
        assert names == ["Alice", "Bob", "Charlie"]


# =============================================================================
# Wire schema
# =============================================================================


class TestWireSchema:
    def test_contact_out_includes_all_fields(self, authed_client: TestClient) -> None:
        response = authed_client.post(
            "/productivity/contacts",
            json={
                "name": "Ada",
                "given_name": "Ada",
                "family_name": "Lovelace",
                "additional_names": ["M"],
                "name_prefix": "Dr.",
                "name_suffix": "Jr.",
                "nickname": "The Enchantress",
                "organization": "ACME",
                "title": "Programmer",
                "emails": ["ada@example.com"],
                "phones": ["+1"],
                "urls": ["https://x"],
                "addresses": ["addr"],
                "note": "note",
                "birthday": "1815-12-10",
            },
        )
        body = response.json()
        for key in (
            "uid",
            "name",
            "given_name",
            "family_name",
            "additional_names",
            "name_prefix",
            "name_suffix",
            "nickname",
            "organization",
            "title",
            "emails",
            "phones",
            "urls",
            "addresses",
            "note",
            "birthday",
            "created",
            "modified",
        ):
            assert key in body, f"missing key: {key}"

    def test_birthday_serialised_as_iso_date(self, authed_client: TestClient) -> None:
        response = authed_client.post(
            "/productivity/contacts",
            json={"name": "Ada", "birthday": "1815-12-10"},
        )
        assert response.json()["birthday"] == "1815-12-10"
