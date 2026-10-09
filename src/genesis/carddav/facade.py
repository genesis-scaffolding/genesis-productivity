"""Facade for CardDAV operations.

The :class:`CardDAVFacade` is a single entry point for the
contact layer. It owns a :class:`CardDAVWebDAV` client (which
wraps :class:`caldav.DAVClient`) and caches the per-user
addressbook URL on first access so the five CRUD methods can
delegate to :mod:`genesis.carddav.contacts` without re-running
the MKCOL / PROPFIND dance on every call.

Mirrors :class:`genesis.caldav.facade.CalDAVFacade`:
``server_url`` / ``username`` / ``password`` come from the same
DAV config block; ``new_uid`` is shared (see ADR-016).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..entities import Contact, ContactCreate, ContactUpdate
from . import contacts
from .webdav import CardDAVWebDAV

if TYPE_CHECKING:
    pass


class CardDAVFacade:
    """Facade for CardDAV access to contacts.

    Constructs lazily: the underlying :class:`caldav.DAVClient`
    is built on first access of :attr:`_webdav`, and the
    addressbook URL is discovered on the first CRUD call.
    """

    def __init__(self, server_url: str, username: str, password: str) -> None:
        self._server_url = server_url
        self._username = username
        self._password = password
        self._webdav: CardDAVWebDAV | None = None
        self._addressbook_url: str | None = None

    @property
    def webdav(self) -> CardDAVWebDAV:
        """Return the lazy :class:`CardDAVWebDAV` client.

        Public so the integration tests can drive the same
        plumbing the production facade uses without going
        through CRUD. Production callers should use the
        ``create_contact`` / ``get_contact`` / etc. methods.
        """
        if self._webdav is None:
            self._webdav = CardDAVWebDAV(
                server_url=self._server_url,
                username=self._username,
                password=self._password,
            )
        return self._webdav

    def create_contact(self, data: ContactCreate) -> Contact:
        return contacts.create_contact(self, data)

    def get_contact(self, uid: str) -> Contact | None:
        return contacts.get_contact(self, uid)

    def list_contacts(
        self,
        *,
        search: str | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "asc",
    ) -> list[Contact]:
        return contacts.list_contacts(self, search=search, sort_by=sort_by, sort_order=sort_order)

    def update_contact(self, uid: str, data: ContactUpdate) -> Contact | None:
        return contacts.update_contact(self, uid, data)

    def delete_contact(self, uid: str) -> bool:
        return contacts.delete_contact(self, uid)

    def search_contacts(self, query: str) -> list[Contact]:
        """Back-compat wrapper around :meth:`list_contacts`."""
        return contacts.search_contacts(self, query)


# Re-export the entity so callers can ``from genesis.carddav.facade
# import Contact`` exactly the way they used to.
__all__ = ["CardDAVFacade", "Contact"]
