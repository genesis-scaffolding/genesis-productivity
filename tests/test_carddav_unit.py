"""Unit tests for the CardDAV facade using a mock backend.

Tests the in-process :class:`tests.mock_carddav.MockCardDAVFacade`
through the same five public methods the real facade exposes,
covering the public contract only.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest

from genesis.entities import ContactCreate, ContactUpdate
from tests.mock_carddav import MockCardDAVFacade


@pytest.fixture
def facade() -> MockCardDAVFacade:
    return MockCardDAVFacade()


# =============================================================================
# CRUD
# =============================================================================


class TestContactCRUD:
    def test_create_returns_contact_with_uid_and_timestamps(self, facade: MockCardDAVFacade) -> None:
        contact = facade.create_contact(ContactCreate(name="Ada Lovelace"))
        assert contact.uid is not None
        assert contact.uid.startswith("c-")
        assert contact.name == "Ada Lovelace"
        assert contact.created is not None
        assert contact.modified is not None

    def test_create_stores_all_fields(self, facade: MockCardDAVFacade) -> None:
        contact = facade.create_contact(
            ContactCreate(
                name="Ada Lovelace",
                given_name="Ada",
                family_name="Lovelace",
                additional_names=["M"],
                name_prefix="Dr.",
                name_suffix="Jr.",
                nickname="The Enchantress",
                organization="Analytical Engine Co",
                title="Programmer",
                emails=["ada@example.com", "ada@math.org"],
                phones=["+44 20 7946 0958"],
                urls=["https://example.com/ada"],
                addresses=["10 St James Square, London"],
                note="First programmer",
                birthday=date(1815, 12, 10),
            )
        )
        assert contact.given_name == "Ada"
        assert contact.family_name == "Lovelace"
        assert contact.additional_names == ["M"]
        assert contact.nickname == "The Enchantress"
        assert contact.organization == "Analytical Engine Co"
        assert contact.emails == ["ada@example.com", "ada@math.org"]
        assert contact.phones == ["+44 20 7946 0958"]
        assert contact.birthday == date(1815, 12, 10)

    def test_get_contact_returns_stored_contact(self, facade: MockCardDAVFacade) -> None:
        created = facade.create_contact(ContactCreate(name="Ada Lovelace"))
        fetched = facade.get_contact(created.uid)
        assert fetched is not None
        assert fetched.name == "Ada Lovelace"

    def test_get_contact_missing_returns_none(self, facade: MockCardDAVFacade) -> None:
        assert facade.get_contact("c-nonexistent") is None

    def test_delete_contact_returns_true_then_false(self, facade: MockCardDAVFacade) -> None:
        created = facade.create_contact(ContactCreate(name="Ada Lovelace"))
        assert facade.delete_contact(created.uid) is True
        assert facade.delete_contact(created.uid) is False
        assert facade.get_contact(created.uid) is None

    def test_update_contact_modifies_fields(self, facade: MockCardDAVFacade) -> None:
        created = facade.create_contact(ContactCreate(name="Ada Lovelace"))
        updated = facade.update_contact(created.uid, ContactUpdate(name="Augusta Ada King-Noel"))
        assert updated is not None
        assert updated.name == "Augusta Ada King-Noel"
        assert updated.uid == created.uid
        assert updated.modified is not None
        assert created.modified is not None
        assert updated.modified >= created.modified

    def test_update_contact_can_clear_optional_field(self, facade: MockCardDAVFacade) -> None:
        created = facade.create_contact(ContactCreate(name="Ada", organization="ACME"))
        updated = facade.update_contact(created.uid, ContactUpdate(clear_organization=True))
        assert updated is not None
        assert updated.organization is None

    def test_update_contact_missing_returns_none(self, facade: MockCardDAVFacade) -> None:
        result = facade.update_contact("c-nope", ContactUpdate(name="X"))
        assert result is None

    def test_update_replaces_multi_value_lists_wholesale(self, facade: MockCardDAVFacade) -> None:
        created = facade.create_contact(ContactCreate(name="Ada", emails=["ada@old.com", "ada@ancient.com"]))
        updated = facade.update_contact(created.uid, ContactUpdate(emails=["ada@new.com"]))
        assert updated is not None
        assert updated.emails == ["ada@new.com"]


# =============================================================================
# Search and sort
# =============================================================================


class TestListAndSearch:
    def test_list_contacts_returns_all(self, facade: MockCardDAVFacade) -> None:
        facade.create_contact(ContactCreate(name="Alice"))
        facade.create_contact(ContactCreate(name="Bob"))
        assert len(facade.list_contacts()) == 2

    def test_list_contacts_search_matches_name(self, facade: MockCardDAVFacade) -> None:
        facade.create_contact(ContactCreate(name="Ada Lovelace"))
        facade.create_contact(ContactCreate(name="Alice Cooper", emails=["alice@example.com"]))
        # "ada" matches both: Ada Lovelace (name) and Alice Cooper (no match).
        result = facade.list_contacts(search="ada")
        names = {c.name for c in result}
        # The search is across name / org / nickname / emails.
        # "ada" is a substring of "Ada Lovelace" (name) but not of
        # "Alice Cooper" (name or emails) so only Ada is returned.
        assert names == {"Ada Lovelace"}

    def test_list_contacts_search_matches_email(self, facade: MockCardDAVFacade) -> None:
        facade.create_contact(ContactCreate(name="Ada", emails=["ada@example.com"]))
        facade.create_contact(ContactCreate(name="Bob", emails=["bob@example.com"]))
        result = facade.list_contacts(search="ada@")
        assert [c.name for c in result] == ["Ada"]

    def test_list_contacts_search_matches_organization(self, facade: MockCardDAVFacade) -> None:
        facade.create_contact(ContactCreate(name="Alice", organization="ACME"))
        facade.create_contact(ContactCreate(name="Bob", organization="Other"))
        result = facade.list_contacts(search="acme")
        assert [c.name for c in result] == ["Alice"]

    def test_list_contacts_search_matches_nickname(self, facade: MockCardDAVFacade) -> None:
        facade.create_contact(ContactCreate(name="Alice", nickname="Ali"))
        facade.create_contact(ContactCreate(name="Bob"))
        result = facade.list_contacts(search="ali")
        assert [c.name for c in result] == ["Alice"]

    def test_list_contacts_search_is_case_insensitive(self, facade: MockCardDAVFacade) -> None:
        facade.create_contact(ContactCreate(name="Alice Cooper"))
        assert len(facade.list_contacts(search="ALICE")) == 1
        assert len(facade.list_contacts(search="alice")) == 1

    def test_list_contacts_search_no_match_returns_empty(self, facade: MockCardDAVFacade) -> None:
        facade.create_contact(ContactCreate(name="Alice"))
        assert facade.list_contacts(search="zzz") == []

    def test_list_contacts_sort_by_name_ascending(self, facade: MockCardDAVFacade) -> None:
        facade.create_contact(ContactCreate(name="Charlie"))
        facade.create_contact(ContactCreate(name="Alice"))
        facade.create_contact(ContactCreate(name="Bob"))
        result = facade.list_contacts(sort_by=["name"])
        assert [c.name for c in result] == ["Alice", "Bob", "Charlie"]

    def test_list_contacts_sort_by_name_descending(self, facade: MockCardDAVFacade) -> None:
        facade.create_contact(ContactCreate(name="Alice"))
        facade.create_contact(ContactCreate(name="Charlie"))
        facade.create_contact(ContactCreate(name="Bob"))
        result = facade.list_contacts(sort_by=["name"], sort_order="desc")
        assert [c.name for c in result] == ["Charlie", "Bob", "Alice"]

    def test_list_contacts_default_order_is_stable(self, facade: MockCardDAVFacade) -> None:
        a = facade.create_contact(ContactCreate(name="A"))
        b = facade.create_contact(ContactCreate(name="B"))
        c = facade.create_contact(ContactCreate(name="C"))
        result = facade.list_contacts()
        # Default sort is (created, uid), which matches insertion
        # order for a single process.
        assert [x.uid for x in result] == [a.uid, b.uid, c.uid]

    def test_search_contacts_back_compat_wrapper(self, facade: MockCardDAVFacade) -> None:
        facade.create_contact(ContactCreate(name="Ada Lovelace"))
        facade.create_contact(ContactCreate(name="Bob"))
        result = facade.search_contacts("ada")
        assert [c.name for c in result] == ["Ada Lovelace"]


# =============================================================================
# Multi-value round-trips
# =============================================================================


class TestMultiValueFields:
    def test_multi_value_round_trip_through_orm(self, facade: MockCardDAVFacade) -> None:
        created = facade.create_contact(
            ContactCreate(
                name="Multi",
                emails=["a@x.com", "b@x.com"],
                phones=["+1", "+2"],
                urls=["https://a", "https://b"],
                addresses=["addr1", "addr2"],
            )
        )
        # Read back through a fresh facade call so we test the
        # stored data, not the original object reference.
        fetched = facade.get_contact(created.uid)
        assert fetched is not None
        assert fetched.emails == ["a@x.com", "b@x.com"]
        assert fetched.phones == ["+1", "+2"]
        assert fetched.urls == ["https://a", "https://b"]
        assert fetched.addresses == ["addr1", "addr2"]

    def test_empty_multi_value_fields_default_to_empty_list(self, facade: MockCardDAVFacade) -> None:
        created = facade.create_contact(ContactCreate(name="Bare"))
        assert created.emails == []
        assert created.phones == []
        assert created.urls == []
        assert created.addresses == []


class TestCaldavLogFilter:
    """The caldav library warns on every ``text/vcard`` response.

    The warning is benign (the body is read correctly via the
    ``_raw`` attribute) but noisy. ``genesis.carddav.webdav``
    installs a logger filter at import time that drops the
    warning; these tests pin the contract so a future library
    upgrade that removes the warning (or changes the message
    text) is caught immediately.
    """

    def test_filter_drops_text_vcard_content_type_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("caldav")
        with caplog.at_level(logging.WARNING, logger="caldav"):
            logger.warning("Deviation from expectations found: Unexpected content type: text/vcard; charset=utf-8")
        # The filter is scoped narrowly to this message; the
        # record is filtered out before it reaches the handlers.
        assert not any("Unexpected content type: text/vcard" in r.getMessage() for r in caplog.records)

    def test_filter_preserves_other_caldav_warnings(self, caplog: pytest.LogCaptureFixture) -> None:
        logger = logging.getLogger("caldav")
        with caplog.at_level(logging.WARNING, logger="caldav"):
            logger.warning("Some other warning that should pass through")
        assert any("Some other warning" in r.getMessage() for r in caplog.records)
