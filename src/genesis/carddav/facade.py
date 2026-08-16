"""Facade for CardDAV operations.

Provides a single entry point for contacts access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel


class Contact(BaseModel):
    """A contact item."""

    uid: str
    name: str
    email: str | None = None
    phone: str | None = None
    organization: str | None = None


class CardDAVFacade:
    """Facade for CardDAV access to contacts.

    This is a placeholder. Actual implementation will connect to a
    CardDAV server and sync contacts.
    """

    def __init__(self, server_url: str, username: str, password: str) -> None:
        self._server_url = server_url
        self._username = username
        self._password = password

    def get_contacts(self) -> list[Contact]:
        """Return all contacts."""
        return []

    def search_contacts(self, query: str) -> list[Contact]:
        """Search contacts by name, email, or organization."""
        return []
