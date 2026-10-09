"""Contact endpoints: list, create, read, update, delete.

Mirrors the productivity router shape: per-request
:class:`Facade` build, bearer-token auth via
:func:`current_subject`, and entity-to-wire translation in a
single ``_contact_to_wire`` helper. Mounted under
``/productivity/contacts`` so the existing router prefix keeps
every domain endpoint under one OpenAPI tag.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ...entities import ContactCreate, ContactUpdate
from ..auth import current_subject
from ..schemas import (
    ContactCreateIn,
    ContactListOut,
    ContactOut,
    ContactUpdateIn,
)
from .productivity import _facade, _paginate

router = APIRouter(prefix="/productivity/contacts", tags=["productivity"])


def _contact_to_wire(contact) -> ContactOut:
    """Translate a :class:`Contact` entity to the wire model."""
    return ContactOut(
        uid=contact.uid,
        name=contact.name,
        given_name=contact.given_name,
        family_name=contact.family_name,
        additional_names=contact.additional_names,
        name_prefix=contact.name_prefix,
        name_suffix=contact.name_suffix,
        nickname=contact.nickname,
        organization=contact.organization,
        title=contact.title,
        emails=contact.emails,
        phones=contact.phones,
        urls=contact.urls,
        addresses=contact.addresses,
        note=contact.note,
        birthday=contact.birthday,
        created=contact.created,
        modified=contact.modified,
    )


@router.get("", response_model=ContactListOut)
def list_contacts(
    subject: Annotated[str, Depends(current_subject)],
    search_query: Annotated[str | None, Query(alias="q")] = None,
    sort_by: Annotated[list[str] | None, Query()] = None,
    order: Annotated[str, Query()] = "asc",
    offset: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query()] = 50,
) -> ContactListOut:
    """List contacts. Search is a case-insensitive substring
    match across name, organization, nickname, and emails.
    """
    facade = _facade(subject)
    try:
        all_contacts = facade.carddav.list_contacts(
            search=search_query,
            sort_by=sort_by,
            sort_order=order,
        )
        items = [_contact_to_wire(c) for c in all_contacts]
        page, total, offset, limit = _paginate(items, offset, limit)
        return ContactListOut(items=page, total=total, offset=offset, limit=limit)
    finally:
        facade.shutdown()


@router.post("", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
def create_contact(
    body: ContactCreateIn,
    subject: Annotated[str, Depends(current_subject)],
) -> ContactOut:
    facade = _facade(subject)
    try:
        contact = facade.carddav.create_contact(
            ContactCreate(
                name=body.name,
                given_name=body.given_name,
                family_name=body.family_name,
                additional_names=body.additional_names,
                name_prefix=body.name_prefix,
                name_suffix=body.name_suffix,
                nickname=body.nickname,
                organization=body.organization,
                title=body.title,
                emails=body.emails,
                phones=body.phones,
                urls=body.urls,
                addresses=body.addresses,
                note=body.note,
                birthday=body.birthday,
            )
        )
        return _contact_to_wire(contact)
    finally:
        facade.shutdown()


@router.get("/{uid}", response_model=ContactOut)
def read_contact(
    uid: str,
    subject: Annotated[str, Depends(current_subject)],
) -> ContactOut:
    facade = _facade(subject)
    try:
        contact = facade.carddav.get_contact(uid)
        if contact is None:
            raise HTTPException(status_code=404, detail="Contact not found")
        return _contact_to_wire(contact)
    finally:
        facade.shutdown()


@router.patch("/{uid}", response_model=ContactOut)
def update_contact(
    uid: str,
    body: ContactUpdateIn,
    subject: Annotated[str, Depends(current_subject)],
) -> ContactOut:
    facade = _facade(subject)
    try:
        existing = facade.carddav.get_contact(uid)
        if existing is None:
            raise HTTPException(status_code=404, detail="Contact not found")

        kwargs: dict[str, Any] = {}
        for field_name in (
            "name",
            "given_name",
            "family_name",
            "additional_names",
            "name_prefix",
            "name_suffix",
            "nickname",
            "organization",
            "title",
            "emails",
            "phones",
            "urls",
            "addresses",
            "note",
            "birthday",
        ):
            value = getattr(body, field_name)
            if value is not None:
                kwargs[field_name] = value
        for clear_field in (
            "clear_given_name",
            "clear_family_name",
            "clear_name_prefix",
            "clear_name_suffix",
            "clear_nickname",
            "clear_organization",
            "clear_title",
            "clear_note",
            "clear_birthday",
        ):
            if getattr(body, clear_field):
                kwargs[clear_field] = True

        if not kwargs:
            return _contact_to_wire(existing)

        contact = facade.carddav.update_contact(uid, ContactUpdate(**kwargs))
        if contact is None:
            raise HTTPException(status_code=409, detail="Contact was modified concurrently")
        return _contact_to_wire(contact)
    finally:
        facade.shutdown()


@router.delete("/{uid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contact(
    uid: str,
    subject: Annotated[str, Depends(current_subject)],
) -> None:
    facade = _facade(subject)
    try:
        if not facade.carddav.delete_contact(uid):
            raise HTTPException(status_code=404, detail="Contact not found")
    finally:
        facade.shutdown()
