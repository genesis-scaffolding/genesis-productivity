"""Integration tests for the contact API against a real Radicale.

Mirrors the productivity round-trip suite. Slow (~1 s per test
for the Radicale subprocess startup) so the suite is gated
behind :func:`pytest.mark.integration`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def authed_client(api_server_real: TestClient) -> TestClient:
    """Yield a TestClient backed by a real Radicale with a token."""
    response = api_server_real.post(
        "/auth/login",
        data={"username": "default", "password": "test-pw", "grant_type": "password"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    api_server_real.headers["Authorization"] = f"Bearer {token}"
    return api_server_real


@pytest.mark.integration
class TestContactAPIRoundTrip:
    def test_create_then_get_against_real_radicale(self, authed_client: TestClient) -> None:
        response = authed_client.post(
            "/productivity/contacts",
            json={
                "name": "Ada Lovelace",
                "given_name": "Ada",
                "family_name": "Lovelace",
                "emails": ["ada@example.com"],
            },
        )
        assert response.status_code == 201
        body = response.json()
        uid = body["uid"]
        assert uid.startswith("c-")

        # GET round-trips through the real Radicale vCard store.
        response = authed_client.get(f"/productivity/contacts/{uid}")
        assert response.status_code == 200
        fetched = response.json()
        assert fetched["name"] == "Ada Lovelace"
        assert fetched["emails"] == ["ada@example.com"]

    def test_list_then_delete_against_real_radicale(self, authed_client: TestClient) -> None:
        for i in range(3):
            response = authed_client.post("/productivity/contacts", json={"name": f"C{i}"})
            assert response.status_code == 201

        response = authed_client.get("/productivity/contacts")
        assert response.json()["total"] == 3

        # Delete the first and verify the count drops.
        first_uid = response.json()["items"][0]["uid"]
        assert authed_client.delete(f"/productivity/contacts/{first_uid}").status_code == 204
        assert authed_client.get("/productivity/contacts").json()["total"] == 2
