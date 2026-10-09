"""VCARD 3.0 serialization helpers.

Wraps :mod:`vobject` to translate between :class:`Contact` entities
and the VCARD 3.0 wire format Radicale expects. We pin to 3.0
because Radicale does not parse the vCard 4.0 extension properties
and the upstream :mod:`vobject` defaults to 3.0 anyway.

The functions in this module are pure: they do not talk to the
network. Callers fetch the VCARD text via WebDAV, call
:func:`contact_from_vcard` to get an entity, mutate, then call
:func:`contact_to_vcard` to write the mutation back before
re-PUTting.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import vobject
from vobject.base import Component

from ..caldav.ical import new_uid
from ..entities import Contact
from ..tz import to_utc


def new_contact_uid() -> str:
    """Generate a new typed short UID for a freshly-created contact.

    Wraps :func:`genesis.caldav.ical.new_uid` with the ``c-`` prefix
    so all CalDAV / CardDAV UIDs share the same format (ADR-016).
    """
    return new_uid("c")


# -----------------------------------------------------------------------------
# Entity -> vCard
# -----------------------------------------------------------------------------


def contact_to_vcard(contact: Contact) -> str:
    """Build a VCARD 3.0 string from a :class:`Contact`.

    Sets ``FN`` from ``contact.name``. When ``family_name`` or
    ``given_name`` is set, builds a structured ``N`` so the
    addressbook renders the contact properly. Multi-value fields
    (``emails``, ``phones``, ``urls``, ``addresses``) are added
    as repeated properties; vobject handles the value-list
    escaping. ``BDAY`` is set when ``birthday`` is not None.
    ``REV`` is set to ``modified`` (falling back to ``created``,
    then to ``now_utc``) so the server can detect concurrent
    edits.
    """
    card = vobject.vCard()
    card.add("version").value = "3.0"
    card.add("prodid").value = "-//Genesis//Productivity//EN"
    card.add("uid").value = contact.uid
    card.add("fn").value = contact.name
    card.add("n").value = vobject.vcard.Name(
        family=contact.family_name or "",
        given=contact.given_name or "",
        additional=",".join(contact.additional_names),
        prefix=contact.name_prefix or "",
        suffix=contact.name_suffix or "",
    )
    if contact.nickname:
        card.add("nickname").value = contact.nickname
    if contact.organization:
        card.add("org").value = [contact.organization]
    if contact.title:
        card.add("title").value = contact.title
    for email in contact.emails:
        card.add("email").value = email
    for phone in contact.phones:
        card.add("tel").value = phone
    for url in contact.urls:
        card.add("url").value = url
    for addr in contact.addresses:
        card.add("adr").value = vobject.vcard.Address(street=addr)
    if contact.note:
        card.add("note").value = contact.note
    if contact.birthday is not None:
        # vobject 0.9.9 cannot serialize a :class:`datetime.date`
        # for ``BDAY`` (it routes through the text encoder and
        # crashes on ``s.replace``). Pass the ISO string so the
        # default text behaviour is happy; ``contact_from_vcard``
        # parses it back with :meth:`date.fromisoformat`.
        card.add("bday").value = contact.birthday.isoformat()
    rev_dt = contact.modified or contact.created
    if rev_dt is not None:
        # vobject 0.9.9 has the same crash for ``REV`` as for
        # ``BDAY``: the text encoder cannot handle a ``datetime``
        # value. Pass the ISO string instead; the parser accepts
        # the same shape.
        card.add("rev").value = rev_dt.isoformat()
    return card.serialize()


# -----------------------------------------------------------------------------
# vCard -> entity
# -----------------------------------------------------------------------------


def _prop_value(component: Component, name: str) -> str | None:
    """Return the first string value of a property, or None.

    vobject returns ``vText`` for plain string properties and
    custom classes (e.g. ``vcard.Name``) for structured ones. We
    coerce to ``str`` here for the simple string case.

    Special-cases ``org`` because vobject stores ``ORG`` as a
    list of strings; the entity keeps a single string (the
    company name) and ignores the trailing components.
    """
    prop = component.contents.get(name)
    if not prop:
        return None
    # vobject's stub types declare ``contents`` as ``list[VBase]``
    # but the actual objects (``ContentLine`` and the structured
    # classes) do carry ``value`` at runtime. Treat as object and
    # narrow with isinstance.
    value: object = prop[0].value  # type: ignore[attr-defined]
    if value is None:
        return None
    if name.lower() == "org" and isinstance(value, list):
        return value[0] if value else None
    if isinstance(value, str):
        return value
    return str(value)


def _structured_n(component: Component) -> dict[str, Any]:
    """Pull the structured ``N`` properties into a flat dict.

    Empty fields are normalised to ``None`` so the entity surfaces
    a single ``str | None`` shape rather than ``str | ""``.
    """
    prop = component.contents.get("n")
    if not prop:
        return {
            "given_name": None,
            "family_name": None,
            "additional_names": [],
            "name_prefix": None,
            "name_suffix": None,
        }
    # vobject's ``vcard.Name`` exposes ``family`` / ``given`` /
    # ``additional`` / ``prefix`` / ``suffix`` at runtime but the
    # stub types do not include them. Cast through ``object`` so
    # pyright stops flagging the access.
    n: Any = prop[0].value  # type: ignore[attr-defined]
    additional = n.additional if isinstance(n.additional, str) else (n.additional or "")
    return {
        "given_name": n.given or None,
        "family_name": n.family or None,
        "additional_names": [s for s in additional.split(",") if s] if additional else [],
        "name_prefix": n.prefix or None,
        "name_suffix": n.suffix or None,
    }


def _multi_value(component: Component, name: str) -> list[str]:
    """Return all values of a repeated property as strings.

    ``adr`` is special: vobject parses each ``ADR`` as an
    :class:`vobject.vcard.Address` whose ``street`` field carries
    the single-line blob we wrote on serialise. We pull
    ``street`` so the round-trip preserves the user-typed text
    instead of the seven-component structured form.
    """
    prop = component.contents.get(name)
    if not prop:
        return []
    if name.lower() == "adr":
        result: list[str] = []
        for p in prop:
            # vobject stub types: see note in ``_prop_value``.
            addr = p.value  # type: ignore[attr-defined]
            if addr is not None and getattr(addr, "street", None):
                result.append(str(addr.street))
        return result
    result2: list[str] = []
    for p in prop:
        value = p.value  # type: ignore[attr-defined]
        if value is not None:
            result2.append(str(value))
    return result2


def _bday(component: Component) -> date | None:
    """Extract a ``BDAY`` value as a :class:`date`.

    vobject 0.9.9 returns a ``date`` for bare ISO date strings
    and a ``datetime`` for full ISO date+time strings; both are
    coerced to :class:`date` here. An unparseable string (e.g.
    a custom format) returns ``None`` so a malformed vCard does
    not break the whole list.
    """
    prop = component.contents.get("bday")
    if not prop:
        return None
    value: object = prop[0].value  # type: ignore[attr-defined]
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _rev(component: Component) -> datetime | None:
    """Extract a ``REV`` value as a tz-aware UTC datetime.

    vobject 0.9.9 returns the ``REV`` property as a plain
    string regardless of whether the source was an ISO
    date-time, an ISO date, or a ``datetime`` instance. We
    accept both forms: an ISO date+time string (``YYYY-MM-DDTHH:MM:SS[+HH:MM]``)
    is parsed with :func:`datetime.fromisoformat` and
    coerced to UTC; a bare ISO date (``YYYY-MM-DD``) is
    treated as midnight UTC. A missing or unparseable
    ``REV`` returns ``None``.
    """
    prop = component.contents.get("rev")
    if not prop:
        return None
    value: object = prop[0].value  # type: ignore[attr-defined]
    if isinstance(value, datetime):
        return to_utc(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            try:
                parsed = datetime.combine(date.fromisoformat(value), datetime.min.time())
            except ValueError:
                return None
        return to_utc(parsed)
    return None


def contact_from_vcard(text: str) -> Contact:
    """Build a :class:`Contact` entity from a VCARD string.

    Silently ignores properties the entity does not model (KIND,
    PHOTO, IMPP, etc.). The ``uid`` comes from the vCard's
    ``UID`` property; callers that do not want to fall back to a
    default UID should reject empty UIDs upstream.

    Missing ``FN`` is normalised to an empty string so the
    resulting entity still satisfies the required ``name`` field.
    """
    card = vobject.readOne(text)
    name = _prop_value(card, "fn") or ""
    uid = _prop_value(card, "uid") or ""
    n = _structured_n(card)
    return Contact(
        uid=uid,
        name=name,
        given_name=n["given_name"],
        family_name=n["family_name"],
        additional_names=n["additional_names"],
        name_prefix=n["name_prefix"],
        name_suffix=n["name_suffix"],
        nickname=_prop_value(card, "nickname"),
        organization=_prop_value(card, "org"),
        title=_prop_value(card, "title"),
        emails=_multi_value(card, "email"),
        phones=_multi_value(card, "tel"),
        urls=_multi_value(card, "url"),
        addresses=_multi_value(card, "adr"),
        note=_prop_value(card, "note"),
        birthday=_bday(card),
        created=_rev(card),
        modified=_rev(card),
    )
