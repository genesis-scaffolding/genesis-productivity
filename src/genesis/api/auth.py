"""Authentication endpoints: ``/auth/login``, ``/auth/refresh``, ``/auth/me``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

from . import security
from .schemas import RefreshIn, TokenOut

router = APIRouter(prefix="/auth", tags=["auth"])

# tokenUrl is only used for OpenAPI documentation; the bearer scheme
# is what the dependency below enforces on protected routes.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=True)


def current_subject(token: Annotated[str, Depends(oauth2_scheme)]) -> str:
    """FastAPI dependency: validate the bearer token and return the subject."""
    try:
        return security.decode_token(token, expected_type="access")
    except security.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


@router.post("/login", response_model=TokenOut)
def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenOut:
    """OAuth2 password grant.

    Accepts the standard form-encoded body (username, password,
    grant_type). Returns an access + refresh token pair.

    The username must exist in the users table
    (``genesis.users``); both missing-user and wrong-password
    return the same 401 to avoid leaking which usernames exist.
    """
    if not security.verify_user_credentials(form_data.username, form_data.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenOut(
        access_token=security.create_access_token(form_data.username),
        refresh_token=security.create_refresh_token(form_data.username),
        expires_in=security.ACCESS_TOKEN_TTL_SECONDS,
    )


@router.post("/refresh", response_model=TokenOut)
def refresh(body: Annotated[RefreshIn, Body()]) -> TokenOut:
    """Exchange a valid refresh token for a fresh access + refresh pair."""
    try:
        subject = security.decode_token(body.refresh_token, expected_type="refresh")
    except security.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    return TokenOut(
        access_token=security.create_access_token(subject),
        refresh_token=security.create_refresh_token(subject),
        expires_in=security.ACCESS_TOKEN_TTL_SECONDS,
    )


@router.get("/me")
def me(subject: Annotated[str, Depends(current_subject)]) -> dict[str, str]:
    """Return the username embedded in the bearer token."""
    return {"username": subject}
