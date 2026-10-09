"""Mock CardDAV backend for testing.

Stores contacts in memory and implements the same public
interface as :class:`genesis.carddav.facade.CardDAVFacade`. The
API tests use this so the productivity-style filters,
pagination, and CRUD coverage run in milliseconds instead of
against a real Radicale subprocess.
"""

from __future__ import annotations

from datetime import UTC, datetime

from genesis.carddav.vcard import new_contact_uid
from genesis.entities import Contact, ContactCreate, ContactUpdate


class MockCardDAVFacade:
    """In-memory :class:`CardDAVFacade` substitute.

    Mirrors the public surface of the real facade
    (``create_contact`` / ``get_contact`` / ``list_contacts`` /
    ``update_contact`` / ``delete_contact`` / ``search_contacts``)
    but stores contacts in a ``dict[uid, Contact]``. Timestamps
    are stamped on the entity so the list ordering is
    deterministic across the unit tests.
    """

    def __init__(self) -> None:
        self._contacts: dict[str, Contact] = {}
        self._counter: int = 0
        # Captures the calls made on the facade for tests that
        # verify wiring (e.g. that the API route called
        # ``delete_contact`` exactly once).
        self.delete_calls: list[str] = []
        self.update_calls: list[tuple[str, ContactUpdate]] = []

    def _next(self) -> int:
        self._counter += 1
        return self._counter

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def create_contact(self, data: ContactCreate) -> Contact:
        self._next()
        now = self._now()
        contact = Contact(
            uid=new_contact_uid(),
            name=data.name,
            given_name=data.given_name,
            family_name=data.family_name,
            additional_names=data.additional_names,
            name_prefix=data.name_prefix,
            name_suffix=data.name_suffix,
            nickname=data.nickname,
            organization=data.organization,
            title=data.title,
            emails=data.emails,
            phones=data.phones,
            urls=data.urls,
            addresses=data.addresses,
            note=data.note,
            birthday=data.birthday,
            created=now,
            modified=now,
        )
        self._contacts[contact.uid] = contact
        return contact

    def get_contact(self, uid: str) -> Contact | None:
        return self._contacts.get(uid)

    def list_contacts(
        self,
        *,
        search: str | None = None,
        sort_by: list[str] | None = None,
        sort_order: str = "asc",
    ) -> list[Contact]:
        contacts = list(self._contacts.values())
        if search:
            needle = search.lower()
            contacts = [
                c
                for c in contacts
                if needle in c.name.lower()
                or (c.organization is not None and needle in c.organization.lower())
                or (c.nickname is not None and needle in c.nickname.lower())
                or any(needle in e.lower() for e in c.emails)
            ]
        if sort_by:
            for key in reversed(sort_by):
                contacts.sort(
                    key=lambda c, k=key: getattr(c, k, "") or "",
                    reverse=(sort_order == "desc"),
                )
        else:
            contacts.sort(key=lambda c: (c.created or datetime.min.replace(tzinfo=UTC), c.uid))
        return contacts

    def update_contact(self, uid: str, data: ContactUpdate) -> Contact | None:
        self.update_calls.append((uid, data))
        existing = self._contacts.get(uid)
        if existing is None:
            return None
        update_data = data.model_dump(exclude_unset=True)
        clear_fields = {
            name: update_data.pop(name)
            for name in (
                "clear_given_name",
                "clear_family_name",
                "clear_name_prefix",
                "clear_name_suffix",
                "clear_nickname",
                "clear_organization",
                "clear_title",
                "clear_note",
                "clear_birthday",
            )
            if name in update_data
        }
        for key, value in update_data.items():
            setattr(existing, key, value)
        for name, flag in clear_fields.items():
            if flag:
                field_name = name[len("clear_") :]
                setattr(existing, field_name, None)
        existing.modified = self._now()
        return existing

    def delete_contact(self, uid: str) -> bool:
        self.delete_calls.append(uid)
        if uid not in self._contacts:
            return False
        del self._contacts[uid]
        return True

    def search_contacts(self, query: str) -> list[Contact]:
        """Back-compat wrapper matching the real facade."""
        return self.list_contacts(search=query)


__all__ = ["MockCardDAVFacade"]
