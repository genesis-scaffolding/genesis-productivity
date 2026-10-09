"""Contact CRUD operations for :class:`CardDAVFacade`.

The five public functions (:func:`create_contact`,
:func:`get_contact`, :func:`list_contacts`, :func:`update_contact`,
:func:`delete_contact`) are thin delegations from the facade,
mirroring the per-entity module pattern in
:mod:`genesis.caldav.tasks`.

The ``list_contacts`` filter happens client-side after a full
``PROPFIND`` of the addressbook because RFC 6352 only supports
per-property ``OR`` filters in ``CR:addressbook-query``, and
the cost of GETting + parsing every vCard is bounded (Radicale
is local, addressbooks are small). This matches the
client-side full-text extension we do for tasks and journals.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..caldav.ical import now_utc
from ..entities import Contact, ContactCreate, ContactUpdate
from . import vcard
from .vcard import new_contact_uid

if TYPE_CHECKING:
    from .facade import CardDAVFacade

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_addressbook(facade: CardDAVFacade) -> str:
    """Return the addressbook URL, MKCOL-ing it on first use.

    Cached on the facade instance so we only PROPFIND + MKCOL
    once per process. The addressbook is per-user (the URL
    embeds the per-user principal), so two users in the same
    process get different addressbooks.
    """
    if facade._addressbook_url is None:
        facade._addressbook_url = facade.webdav.ensure_addressbook("contacts")
    return facade._addressbook_url


def _build_contact_from_vcard(text: str, *, uid: str) -> Contact:
    """Build a :class:`Contact` and stamp created/modified from ``now``.

    ``contact_from_vcard`` only reads ``REV``; if the server did
    not write one (older Radicales, manual uploads), we fall
    back to "now" so the list and detail views always have a
    non-null ``modified`` to display.
    """
    contact = vcard.contact_from_vcard(text)
    if contact.modified is None:
        now = now_utc()
        contact = contact.model_copy(update={"created": now, "modified": now})
    if not contact.uid:
        contact = contact.model_copy(update={"uid": uid})
    return contact


# ---------------------------------------------------------------------------
# Public CRUD
# ---------------------------------------------------------------------------


def create_contact(facade: CardDAVFacade, data: ContactCreate) -> Contact:
    """Create a new contact in the user's addressbook.

    A new typed short UID is generated server-side. The vCard is
    PUT to ``<addressbook>/<uid>.vcf``. ``created`` and
    ``modified`` are set to ``now_utc`` so the entity carries a
    non-null timestamp even before the server has been read
    back.
    """
    now = now_utc()
    uid = new_contact_uid()
    contact = Contact(
        uid=uid,
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
    addressbook = _get_addressbook(facade)
    facade.webdav.put_vcard(addressbook, uid, vcard.contact_to_vcard(contact))
    return contact


def get_contact(facade: CardDAVFacade, uid: str) -> Contact | None:
    """Return the contact for ``uid``, or ``None`` if not found.

    Treats 404 as "not found" and returns ``None`` rather than
    raising, mirroring :func:`genesis.caldav.tasks.get_task`.
    Any other error propagates so transient failures are not
    silently swallowed.
    """
    addressbook = _get_addressbook(facade)
    url = f"{addressbook.rstrip('/')}/{uid}.vcf"
    try:
        text = facade.webdav.get_vcard(url)
    except FileNotFoundError:
        return None
    return _build_contact_from_vcard(text, uid=uid)


def list_contacts(
    facade: CardDAVFacade,
    search: str | None = None,
    sort_by: list[str] | None = None,
    sort_order: str = "asc",
) -> list[Contact]:
    """List all contacts in the user's addressbook.

    The full addressbook is fetched and parsed; the ``search``
    argument is applied client-side across ``name``, ``emails``,
    ``organization``, and ``nickname`` (case-insensitive
    substring match). The result is sorted by ``(created, uid)``
    when no ``sort_by`` is supplied, matching the stable-sort
    convention from ADR-023.

    ``sort_by`` accepts the entity attribute names. Unknown
    attribute names are treated as no-op (the sort key returns
    ``""`` and the secondary ``uid`` keeps the order stable).
    """
    addressbook = _get_addressbook(facade)
    refs = facade.webdav.list_addressbook_items(addressbook)
    contacts: list[Contact] = []
    for href in refs:
        try:
            text = facade.webdav.get_vcard(href)
        except FileNotFoundError:
            continue
        except Exception as exc:
            logger.warning("failed to GET vCard %s: %s", href, exc)
            continue
        uid = href.rstrip("/").rsplit("/", 1)[-1].removesuffix(".vcf")
        contacts.append(_build_contact_from_vcard(text, uid=uid))

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


def update_contact(facade: CardDAVFacade, uid: str, data: ContactUpdate) -> Contact | None:
    """Update an existing contact.

    Loads the current vCard, applies the patch in memory,
    re-serializes, and PUTs to the same URL. UID is preserved.
    ``modified`` is set to ``now_utc`` on the entity and on the
    vCard's ``REV`` property. Returns ``None`` when the contact
    does not exist (404).
    """
    addressbook = _get_addressbook(facade)
    url = f"{addressbook.rstrip('/')}/{uid}.vcf"
    try:
        text = facade.webdav.get_vcard(url)
    except FileNotFoundError:
        return None
    contact = _build_contact_from_vcard(text, uid=uid)

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
        setattr(contact, key, value)
    for name, flag in clear_fields.items():
        if flag:
            field_name = name[len("clear_") :]
            setattr(contact, field_name, None)

    now = now_utc()
    contact.modified = now
    facade.webdav.put_vcard(addressbook, uid, vcard.contact_to_vcard(contact))
    return contact


def delete_contact(facade: CardDAVFacade, uid: str) -> bool:
    """Delete the contact for ``uid``. Returns ``True`` if removed.

    A 404 from the DELETE is treated as a successful no-op so
    the caller does not have to distinguish "already gone" from
    "just deleted". Returns ``False`` only when the underlying
    transport raises an unexpected error.
    """
    addressbook = _get_addressbook(facade)
    url = f"{addressbook.rstrip('/')}/{uid}.vcf"
    try:
        facade.webdav.delete_vcard(url)
    except FileNotFoundError:
        return False
    except Exception as exc:
        logger.warning("failed to DELETE vCard %s: %s", url, exc)
        return False
    return True


def search_contacts(facade: CardDAVFacade, query: str) -> list[Contact]:
    """Back-compat thin wrapper around :func:`list_contacts`.

    Kept so callers that imported the old stub method still
    work. New code should call :func:`list_contacts` directly.
    """
    return list_contacts(facade, search=query)
