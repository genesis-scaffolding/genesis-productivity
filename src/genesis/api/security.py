"""JWT helpers for the agent API.

Shared signing key (ADR-019). One Radicale serves every user and
one JWT signing key signs every token. Rotating the key
(deleting ``<XDG_DATA_HOME>/genesis/api_secret.key``) invalidates
every user's tokens at once, forcing a coordinated re-login.

Tokens are HS256 JWTs signed with a secret persisted to
``~/.local/share/genesis/api_secret.key`` (mode 0600, generated
on first call).

We use ``hmac`` and ``hashlib`` from the standard library instead
of PyJWT — HS256 only needs these primitives.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

from ..configs import DEFAULT_APP_NAME, DEFAULT_DATA_HOME
from ..users import verify_credentials

ACCESS_TOKEN_TTL_SECONDS = 10 * 60 * 60  # 10 hours
REFRESH_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


def secret_path_for(username: str) -> Path:
    """Return the path to the (single, shared) JWT signing secret.

    The ``username`` argument is kept for backward compatibility
    with call sites that pass the JWT subject; the value is
    ignored. The shared secret lives at
    ``<XDG_DATA_HOME>/<app_name>/api_secret.key`` and signing a
    token with it means "this token is valid for whichever user
    is in the ``sub`` claim". Rotating the file invalidates every
    outstanding token at once.
    """
    data_home = Path(os.environ.get("XDG_DATA_HOME", str(DEFAULT_DATA_HOME)))
    return data_home / DEFAULT_APP_NAME / "api_secret.key"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _load_or_create_secret(path: Path) -> bytes:
    """Return the per-user secret key, creating it on first call.

    The file is written with mode 0o600 so other users on the host
    cannot read the secret.
    """
    if path.exists():
        return path.read_bytes()

    path.parent.mkdir(parents=True, exist_ok=True)
    secret = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, secret)
    finally:
        os.close(fd)
    return secret


def _load_secret(path: Path) -> bytes | None:
    """Return the per-user secret key, or ``None`` if it does not exist.

    Used on the decode path so a forged token claiming a
    non-existent subject cannot trigger creation of an empty secret
    file.
    """
    if not path.exists():
        return None
    return path.read_bytes()


def _sign(secret: bytes, signing_input: bytes) -> bytes:
    """Return HMAC-SHA256 digest for the JWT signing input."""
    return hmac.new(secret, signing_input, hashlib.sha256).digest()


class InvalidTokenError(Exception):
    """Raised when a token is malformed, has a bad signature, or has
    the wrong type for the requested operation."""


def verify_user_credentials(username: str, password: str) -> bool:
    """Thin wrapper that keeps the API module's call sites readable."""
    return verify_credentials(username, password)


def _make_token(
    *,
    subject: str,
    token_type: str,
    expires_in: int,
) -> str:
    secret = _load_or_create_secret(secret_path_for(subject))
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_in,
    }
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = _sign(secret, signing_input)
    sig_b64 = _b64url_encode(sig)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def create_access_token(subject: str) -> str:
    return _make_token(
        subject=subject,
        token_type="access",
        expires_in=ACCESS_TOKEN_TTL_SECONDS,
    )


def create_refresh_token(subject: str) -> str:
    return _make_token(
        subject=subject,
        token_type="refresh",
        expires_in=REFRESH_TOKEN_TTL_SECONDS,
    )


def decode_token(token: str, *, expected_type: str) -> str:
    """Validate ``token`` and return the subject.

    Raises ``InvalidTokenError`` on:
    - malformed structure
    - bad signature
    - wrong ``type`` claim
    - expired (``exp`` in the past)
    """
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError as e:
        raise InvalidTokenError(f"malformed token: {e}") from None

    try:
        signing_input = f"{header_b64}.{payload_b64}".encode()
        sig = _b64url_decode(sig_b64)
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as e:
        raise InvalidTokenError(f"malformed token payload: {e}") from None

    if header.get("alg") != "HS256":
        raise InvalidTokenError(f"unsupported alg: {header.get('alg')}")

    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise InvalidTokenError("missing or invalid sub")

    secret = _load_secret(secret_path_for(sub))
    if secret is None:
        raise InvalidTokenError("no signing key for subject")
    expected_sig = _sign(secret, signing_input)
    if not hmac.compare_digest(sig, expected_sig):
        raise InvalidTokenError("bad signature")

    if payload.get("type") != expected_type:
        raise InvalidTokenError(f"expected token type {expected_type!r}, got {payload.get('type')!r}")

    exp = payload.get("exp")
    if not isinstance(exp, int) or exp <= int(time.time()):
        raise InvalidTokenError("token expired")

    return sub
