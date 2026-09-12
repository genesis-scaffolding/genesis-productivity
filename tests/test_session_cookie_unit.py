"""Unit tests for ``genesis.ui._session_cookie``.

Covers the HMAC sign / verify round-trip, expiry and TTL bound
rejection, tampered-signature rejection, malformed-input
handling, secret rotation, and the Set-Cookie header shape on
the response helpers.

The module imports ``streamlit`` at module load time, which is
safe outside a script run. The functions that touch
``st.session_state`` / ``st.context`` (:func:`rehydrate_from_cookie`)
are not exercised here — they need a Streamlit script run context
and are covered by the manual smoke test in the ADR plan.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest

import genesis.ui._session_cookie as cookie
from genesis.ui._session_cookie import (
    INTENT_TTL_SECONDS,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    clear_session_cookie_response,
    set_session_cookie_response,
    sign_intent,
    verify_intent,
)


@pytest.fixture(autouse=True)
def _isolated_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the secret file at a temp path so tests don't share or
    overwrite a developer's real signing key. Also restore the
    module-level ``_load_or_create_secret`` so each test re-reads
    from the new path."""
    secret_path = tmp_path / "session_cookie.key"
    monkeypatch.setattr(cookie, "_SECRET_FILENAME", secret_path.name)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


def test_sign_and_verify_intent_round_trip() -> None:
    """A freshly minted intent verifies as the same username."""
    token = sign_intent("alice")
    assert verify_intent(token) == "alice"


def test_session_cookie_signs_at_max_age() -> None:
    """The session cookie uses ``SESSION_MAX_AGE_SECONDS`` as TTL.

    Verified indirectly: a token whose expiry is
    ``SESSION_MAX_AGE_SECONDS`` in the future verifies via the
    shared ``_verify`` path; ``verify_intent`` rejects it because
    it exceeds the intent TTL bound.
    """
    # Build a long-lived token through the internal helper. This
    # is the same call set_session_cookie_response makes.
    long_token = cookie._sign("alice", ttl_seconds=SESSION_MAX_AGE_SECONDS)
    assert cookie._verify(long_token) == "alice"
    # The intent endpoint must reject a long-lived token even
    # though it is structurally valid.
    assert verify_intent(long_token) is None


def test_verify_rejects_expired_token() -> None:
    """A token whose expiry is in the past returns ``None``."""
    expired = cookie._sign("alice", ttl_seconds=-1)
    assert cookie._verify(expired) is None
    assert verify_intent(expired) is None


def test_verify_rejects_tampered_signature() -> None:
    """Flipping a byte in the signature makes verify return ``None``."""
    token = sign_intent("alice")
    head, sig = token.rsplit(".", 1)
    flipped = "0" if sig[0] != "0" else "1"
    tampered = f"{head}.{flipped}{sig[1:]}"
    assert verify_intent(tampered) is None


def test_verify_rejects_tampered_payload() -> None:
    """Editing the username breaks the HMAC."""
    token = sign_intent("alice")
    expiry_s, _username, sig = token.rsplit(".", 2)
    tampered = f"{expiry_s}.bob.{sig}"
    assert verify_intent(tampered) is None


def test_verify_rejects_malformed_token() -> None:
    """Tokens without exactly three dot-separated parts return ``None``."""
    assert verify_intent("") is None
    assert verify_intent("only-one-part") is None
    assert verify_intent("a.b") is None
    assert verify_intent("a.b.c.d") is None  # too many parts under rsplit(.,2)


def test_verify_rejects_non_integer_expiry() -> None:
    """The expiry must parse as an int; garbage in that field is rejected."""
    parts = sign_intent("alice").rsplit(".", 2)
    bad = f"notanumber.{parts[1]}.{parts[2]}"
    assert verify_intent(bad) is None


def test_secret_rotation_invalidates_existing_tokens() -> None:
    """A token signed under secret A does not verify under secret B.

    Mirrors the operational property: deleting the
    ``session_cookie.key`` file logs every browser out at once.
    """
    token = sign_intent("alice")
    assert verify_intent(token) == "alice"

    # Replace the secret on disk and clear the module cache so the
    # next sign / verify reads the new bytes.
    secret_path = cookie._secret_path()
    new_secret = b"\x42" * 32
    secret_path.write_bytes(new_secret)

    assert verify_intent(token) is None
    # A token signed with the new secret verifies normally.
    fresh = sign_intent("alice")
    assert verify_intent(fresh) == "alice"


def test_secret_file_created_with_0o600_mode(tmp_path: Path) -> None:
    """First call to ``_load_or_create_secret`` creates the key
    file with mode 0o600."""
    # _isolated_secret already points the module at tmp_path; clear
    # the file so the next call recreates it.
    secret_path = cookie._secret_path()
    if secret_path.exists():
        secret_path.unlink()

    cookie._load_or_create_secret()

    assert secret_path.exists()
    mode = secret_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_secret_is_32_random_bytes(tmp_path: Path) -> None:
    """The persisted secret is 32 bytes of os.urandom."""
    cookie._secret_path().unlink(missing_ok=True)
    secret = cookie._load_or_create_secret()
    assert len(secret) == 32
    # 32 bytes of os.urandom should not be all zeros.
    assert secret != b"\x00" * 32


def test_set_session_cookie_response_has_correct_attrs() -> None:
    """The Set-Cookie header carries the cookie name, HttpOnly,
    SameSite=Lax, Path=/, and a Max-Age matching
    SESSION_MAX_AGE_SECONDS."""
    response = set_session_cookie_response("alice")
    set_cookie_header = response.headers["set-cookie"]
    # The Set-Cookie header is a single string with attributes
    # joined by ``; ``. Starlette serialises httponly as
    # ``HttpOnly`` and samesite as ``SameSite=Lax``.
    assert set_cookie_header.startswith(f"{SESSION_COOKIE_NAME}=")
    assert "HttpOnly" in set_cookie_header
    assert "SameSite=lax" in set_cookie_header
    assert "Path=/" in set_cookie_header
    assert f"Max-Age={SESSION_MAX_AGE_SECONDS}" in set_cookie_header


def test_set_cookie_value_is_signed_and_round_trips() -> None:
    """The cookie value set in the response verifies as the
    username we asked for."""
    response = set_session_cookie_response("alice")
    set_cookie_header = response.headers["set-cookie"]
    # The cookie value is the part before the first ``;``.
    cookie_value = set_cookie_header.split(";", 1)[0].split("=", 1)[1]
    assert cookie._verify(cookie_value) == "alice"


def test_clear_session_cookie_response_expires_cookie() -> None:
    """The clear response carries a Set-Cookie with Max-Age=0 for
    the same name and path."""
    response = clear_session_cookie_response()
    set_cookie_header = response.headers["set-cookie"]
    assert set_cookie_header.startswith(f"{SESSION_COOKIE_NAME}=")
    assert "Max-Age=0" in set_cookie_header
    assert "Path=/" in set_cookie_header


def test_constants_match_documented_values() -> None:
    """Guardrail: the TTL constants documented in the module
    docstring must not drift without updating the ADR."""
    assert SESSION_MAX_AGE_SECONDS == 30 * 24 * 60 * 60
    assert INTENT_TTL_SECONDS == 60


def test_intent_token_has_short_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token minted with the intent TTL cannot outlive it
    relative to the verify_intent bound.

    Computed by inspecting the expiry embedded in the token
    directly, since we cannot freeze time without monkeypatching
    ``time.time`` (done here for completeness).
    """
    fixed_now = 1_700_000_000
    monkeypatch.setattr(cookie.time, "time", lambda: fixed_now)
    token = sign_intent("alice")
    expiry = int(token.rsplit(".", 2)[0])
    assert expiry - fixed_now == INTENT_TTL_SECONDS


def test_signature_uses_hmac_sha256() -> None:
    """The signature segment is hex-encoded HMAC-SHA256 of
    ``<expiry>.<username>`` under the persisted secret.

    This pins the algorithm so a future refactor cannot silently
    downgrade to MD5 or unsalted hashing.
    """
    token = sign_intent("alice")
    expiry_s, username, sig = token.rsplit(".", 2)
    secret = cookie._load_or_create_secret()
    expected = hmac.new(secret, f"{expiry_s}.{username}".encode(), hashlib.sha256).hexdigest()
    assert sig == expected
    # Hex HMAC-SHA256 is 64 chars.
    assert len(sig) == 64