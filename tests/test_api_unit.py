"""Unit tests for the agent API: auth helpers and wire schemas.

These run without a real Radicale or FastAPI server; the integration
suite (test_api.py) covers end-to-end round trips.
"""

from __future__ import annotations

import os

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

# ---------------------------------------------------------------------------
# security: JWT and password
# ---------------------------------------------------------------------------


def test_password_default_matches_genesis_local() -> None:
    os.environ.pop(security.DEFAULT_API_PASSWORD_ENV, None)
    assert security.verify_password("genesis-local") is True


def test_password_wrong_value_rejected() -> None:
    assert security.verify_password("nope") is False


def test_password_env_override() -> None:
    os.environ[security.DEFAULT_API_PASSWORD_ENV] = "override-pw"
    try:
        assert security.verify_password("override-pw") is True
        assert security.verify_password("genesis-local") is False
    finally:
        os.environ.pop(security.DEFAULT_API_PASSWORD_ENV, None)


def test_access_token_round_trip() -> None:
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


def test_expired_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token whose ``exp`` is in the past must be rejected."""
    # Build a token using _make_token with a negative TTL.
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
    # Smoke test that the alias imports without error.
    assert UtcDateTime is not None
