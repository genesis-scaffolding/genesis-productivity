"""Contact entity model.

A contact is a person or organisation we want to keep in the
addressbook. The entity is deliberately a small subset of RFC
6350: enough to render a useful contact list and detail view,
without the long tail of vCard 3.0 / 4.0 properties that we do
not yet have a use for.

Multi-value fields (``emails`` / ``phones`` / ``urls`` /
``addresses``) are always ``list[str]`` in the API and entity
layers, even when the contact has only one value. Single-value
fields are all ``str | None``.

Date semantics follow the project convention in
:mod:`genesis.tz`: ``created`` and ``modified`` are tz-aware
UTC datetimes. ``birthday`` is a calendar date with no time
component.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, field_validator

from ..tz import to_utc


class Contact(BaseModel):
    """A contact entity.

    Attributes:
        uid: Unique identifier. Stable across edits.
        name: Full name (FN in vCard). Required.
        given_name: Given name component of N.
        family_name: Family name component of N.
        additional_names: Middle / additional names (N).
        name_prefix: Honorific / prefix (N).
        name_suffix: Suffix (N).
        nickname: NICKNAME.
        organization: ORG (single string; we do not split
            department / company in v1).
        title: TITLE.
        emails: EMAIL values, one per item.
        phones: TEL values, one per item.
        urls: URL values, one per item.
        addresses: Single-line address strings (ADR collapsed).
        note: NOTE.
        birthday: BDAY, calendar date with no time.
        created: When the contact was created (UTC).
        modified: When the contact was last modified (UTC).
    """

    uid: str
    name: str
    given_name: str | None = None
    family_name: str | None = None
    additional_names: list[str] = []
    name_prefix: str | None = None
    name_suffix: str | None = None
    nickname: str | None = None
    organization: str | None = None
    title: str | None = None
    emails: list[str] = []
    phones: list[str] = []
    urls: list[str] = []
    addresses: list[str] = []
    note: str | None = None
    birthday: date | None = None
    created: datetime | None = None
    modified: datetime | None = None

    model_config = {"extra": "ignore"}

    @field_validator("created", "modified", mode="before")
    @classmethod
    def _utc_field(cls, v: object) -> object:
        if isinstance(v, datetime):
            return to_utc(v)
        return v


class ContactCreate(BaseModel):
    """Data for creating a new contact.

    ``name`` is the only required field. Multi-value fields
    default to empty lists so a contact with just a name can be
    created without specifying them.
    """

    name: str
    given_name: str | None = None
    family_name: str | None = None
    additional_names: list[str] = []
    name_prefix: str | None = None
    name_suffix: str | None = None
    nickname: str | None = None
    organization: str | None = None
    title: str | None = None
    emails: list[str] = []
    phones: list[str] = []
    urls: list[str] = []
    addresses: list[str] = []
    note: str | None = None
    birthday: date | None = None

    model_config = {"extra": "ignore"}


class ContactUpdate(BaseModel):
    """Data for updating a contact. All fields optional.

    Multi-value fields are replaced wholesale when supplied. The
    agent API and the UI both render them as a list the user can
    edit; partial-patch semantics for individual list items are
    out of scope for v1.
    """

    name: str | None = None
    given_name: str | None = None
    clear_given_name: bool = False
    family_name: str | None = None
    clear_family_name: bool = False
    additional_names: list[str] | None = None
    name_prefix: str | None = None
    clear_name_prefix: bool = False
    name_suffix: str | None = None
    clear_name_suffix: bool = False
    nickname: str | None = None
    clear_nickname: bool = False
    organization: str | None = None
    clear_organization: bool = False
    title: str | None = None
    clear_title: bool = False
    emails: list[str] | None = None
    phones: list[str] | None = None
    urls: list[str] | None = None
    addresses: list[str] | None = None
    note: str | None = None
    clear_note: bool = False
    birthday: date | None = None
    clear_birthday: bool = False

    model_config = {"extra": "ignore"}


# Type alias used in the API and UI to render multi-value fields
# uniformly. Stays a list of strings to match the entity.
ContactListField = Annotated[list[str], ...]
