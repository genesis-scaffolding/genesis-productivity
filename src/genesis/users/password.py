"""Argon2id wrappers.

Centralised so the parameters can be tuned in one place. The
defaults are the ``argon2-cffi`` defaults, which are sensible as of
2025: time_cost=3, memory_cost=64 MiB, parallelism=4.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_HASHER = PasswordHasher()


def hash_password(plain: str) -> str:
    """Hash ``plain`` with Argon2id and return the encoded hash string."""
    return _HASHER.hash(plain)


def verify_hash(hash_str: str, plain: str) -> bool:
    """Return ``True`` iff ``plain`` matches ``hash_str``.

    Returns ``False`` (not raise) on mismatch so callers can write
    ``if verify_hash(...):`` without a try/except.
    """
    try:
        return _HASHER.verify(hash_str, plain)
    except VerifyMismatchError:
        return False


def needs_rehash(hash_str: str) -> bool:
    """Return ``True`` if ``hash_str`` was produced with weaker parameters
    than the current ones and should be re-hashed on next successful
    verify."""
    return _HASHER.check_needs_rehash(hash_str)
