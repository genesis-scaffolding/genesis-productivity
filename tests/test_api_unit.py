"""Unit tests for the agent API: auth helpers and wire schemas.

These run without a real Radicale or FastAPI server; the integration
suite (test_api.py) covers end-to-end round trips.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from genesis.api import security
from genesis.api.schemas import (
    ProjectCreateIn,
    Status,
    TaskCreateIn,
    UtcDateTime,
    _format_utc_z,
    wire_to_project_status,
    wire_to_task_status,
)
from genesis.users.store import UserStore

# ---------------------------------------------------------------------------
# security: JWT (per-user signing keys) and password verification
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_users(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> UserStore:
    """Seed a default user and a second user, return the store."""
    store_path = tmp_path / "users.yaml"
    store = UserStore(store_path)
    store.add("default", "default-pw")
    store.add("alice", "alice-pw")
    monkeypatch.setattr("genesis.users.store._default_store", store)
    return store


def test_verify_user_credentials_accepts_known_user(seeded_users: UserStore) -> None:
    assert security.verify_user_credentials("default", "default-pw") is True


def test_verify_user_credentials_rejects_wrong_password(
    seeded_users: UserStore,
) -> None:
    assert security.verify_user_credentials("default", "nope") is False


def test_verify_user_credentials_rejects_unknown_user(
    seeded_users: UserStore,
) -> None:
    assert security.verify_user_credentials("ghost", "anything") is False


def test_shared_signing_key_validates_any_subject(
    seeded_users: UserStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the SHARED signing key (ADR-019), a token minted for
    alice validates against the same secret file as one minted
    for bob — the secret file is the same on disk.

    This is the deliberate trade-off of the shared design:
    revoking a user requires rotating the key (which revokes
    every user at once), and a token's ``sub`` claim is
    self-asserted. The shared secret file is protected by its
    0o600 mode and the per-subject Argon2id verification that
    happens at login time.

    Tampering the payload of an existing token still fails —
    HMAC over (header.payload) means re-signing requires the
    secret itself, which the attacker does not have.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    alice_token = security.create_access_token("alice")
    bob_token = security.create_access_token("bob")
    # Both tokens validate against the same secret file. The
    # ``sub`` claim is what downstream code uses to scope the
    # request, not the secret file path.
    assert security.decode_token(alice_token, expected_type="access") == "alice"
    assert security.decode_token(bob_token, expected_type="access") == "bob"

    # Tampering the payload of alice's token still fails — the
    # HMAC signature is over (header.payload), so a tampered
    # payload needs a fresh signature. The attacker does not
    # have the secret.
    import base64
    import json

    parts = alice_token.split(".")

    def _b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    payload = json.loads(security._b64url_decode(parts[1]).decode())
    payload["sub"] = "bob"
    forged = ".".join([parts[0], _b64(json.dumps(payload).encode()), parts[2]])
    with pytest.raises(security.InvalidTokenError, match="bad signature"):
        security.decode_token(forged, expected_type="access")


def test_shared_secret_invalidated_when_file_deleted(
    seeded_users: UserStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rotating the shared secret (deleting the file) invalidates
    every existing token at once — the documented ADR-019
    behaviour.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    token = security.create_access_token("alice")
    assert security.decode_token(token, expected_type="access") == "alice"
    # Delete the shared secret. Subsequent decode attempts must
    # fail because there is no key to verify the signature
    # against.
    secret_path = security.secret_path_for("alice")
    secret_path.unlink()
    with pytest.raises(security.InvalidTokenError):
        security.decode_token(token, expected_type="access")


def test_access_token_round_trip(seeded_users: UserStore) -> None:
    token = security.create_access_token("default")
    subject = security.decode_token(token, expected_type="access")
    assert subject == "default"


def test_refresh_token_rejected_as_access() -> None:
    token = security.create_refresh_token("default")
    with pytest.raises(security.InvalidTokenError):
        security.decode_token(token, expected_type="access")


def test_access_token_rejected_as_refresh() -> None:
    token = security.create_access_token("default")
    with pytest.raises(security.InvalidTokenError):
        security.decode_token(token, expected_type="refresh")


def test_bad_signature_rejected() -> None:
    token = security.create_access_token("default")
    parts = token.split(".")
    # Tamper with the signature segment.
    tampered = ".".join([parts[0], parts[1], parts[2] + "AAAA"])
    with pytest.raises(security.InvalidTokenError):
        security.decode_token(tampered, expected_type="access")


def test_malformed_token_rejected() -> None:
    with pytest.raises(security.InvalidTokenError):
        security.decode_token("not-a-token", expected_type="access")


def test_token_rejected_when_shared_secret_missing(tmp_path: Path) -> None:
    """A token is rejected if the shared secret file does not exist.

    ADR-019: there is one signing key for the whole system. If
    the file is absent, ``decode_token`` cannot find a key to
    verify the signature against and raises ``InvalidTokenError``.
    """
    import os

    os.environ["XDG_DATA_HOME"] = str(tmp_path)
    # No secret on disk: a hand-crafted token must be rejected.
    token = security.create_access_token("default")  # mints a secret for default
    secret_path = security.secret_path_for("default")
    secret_path.unlink()
    with pytest.raises(security.InvalidTokenError):
        security.decode_token(token, expected_type="access")
    os.environ.pop("XDG_DATA_HOME", None)


def test_expired_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token whose ``exp`` is in the past must be rejected."""
    token = security._make_token(  # noqa: SLF001 - testing private helper
        subject="default",
        token_type="access",
        expires_in=-1,
    )
    with pytest.raises(security.InvalidTokenError, match="expired"):
        security.decode_token(token, expected_type="access")


def test_token_ttl_constants_are_reasonable() -> None:
    # The agent extension checks access validity with a 36000-second
    # window; our TTL must be at least that.
    assert security.ACCESS_TOKEN_TTL_SECONDS >= 36000
    assert security.REFRESH_TOKEN_TTL_SECONDS > security.ACCESS_TOKEN_TTL_SECONDS


def test_secret_path_for_returns_shared_path(tmp_path: Path) -> None:
    """ADR-019: one signing key per server. ``secret_path_for``
    ignores the username and returns the same path regardless of
    which subject is requested."""
    import os

    os.environ["XDG_DATA_HOME"] = str(tmp_path)
    a = security.secret_path_for("alice")
    b = security.secret_path_for("bob")
    c = security.secret_path_for("")
    assert a == b == c
    assert a == tmp_path / "genesis" / "api_secret.key"
    os.environ.pop("XDG_DATA_HOME", None)


# ---------------------------------------------------------------------------
# schemas: datetime Z format and status mapping
# ---------------------------------------------------------------------------


def test_format_utc_z_renders_milliseconds_and_z() -> None:
    from datetime import UTC, datetime

    dt = datetime(2026, 9, 7, 14, 30, 5, 123_000, tzinfo=UTC)
    assert _format_utc_z(dt) == "2026-09-07T14:30:05.123Z"


def test_format_utc_z_truncates_microseconds() -> None:
    from datetime import UTC, datetime

    dt = datetime(2026, 9, 7, 14, 30, 5, 123_456, tzinfo=UTC)
    # Microseconds get truncated to milliseconds.
    assert _format_utc_z(dt) == "2026-09-07T14:30:05.123Z"


def test_task_status_mapping_is_bijective() -> None:
    from genesis.entities import ProjectStatus, TaskStatus

    assert wire_to_task_status(Status.NEEDS_ACTION) is TaskStatus.NEEDS_ACTION
    assert wire_to_task_status(Status.IN_PROGRESS) is TaskStatus.IN_PROGRESS
    assert wire_to_task_status(Status.COMPLETED) is TaskStatus.COMPLETED
    assert wire_to_task_status(Status.CANCELED) is TaskStatus.CANCELED
    assert wire_to_project_status(Status.NEEDS_ACTION) is ProjectStatus.NEEDS_ACTION
    assert wire_to_project_status(Status.IN_PROGRESS) is ProjectStatus.IN_PROGRESS
    assert wire_to_project_status(Status.COMPLETED) is ProjectStatus.COMPLETED
    assert wire_to_project_status(Status.CANCELED) is ProjectStatus.CANCELED


def test_task_create_in_default_status_is_needs_action() -> None:
    spec = TaskCreateIn(title="x")
    assert spec.status == Status.NEEDS_ACTION


def test_project_create_in_default_status_is_needs_action() -> None:
    spec = ProjectCreateIn(name="x")
    assert spec.status == Status.NEEDS_ACTION


def test_utc_datetime_alias_is_a_pydantic_annotation() -> None:
    assert UtcDateTime is not None


def test_time_log_entry_out_category_defaults_to_other() -> None:
    """Legacy entries without a category default to OTHER on read.

    Matches the Pydantic safeguard: missing X-CATEGORY on the VTODO
    comes back as OTHER so downstream code never sees a null pointer.
    """
    from genesis.api.schemas import TimeLogEntryOut
    from genesis.category.enums import TimeLogCategory

    spec = TimeLogEntryOut.model_validate(
        {
            "uid": "l-abc",
            "label": "session",
            "start": "2026-09-08T10:00:00.000Z",
            "due": "2026-09-08T10:30:00.000Z",
            "completed_at": None,
            "status": "completed",
            "mode": "countdown",
            "task_id": None,
            "description": None,
            "created": None,
            "modified": None,
        }
    )
    assert spec.category == TimeLogCategory.OTHER
