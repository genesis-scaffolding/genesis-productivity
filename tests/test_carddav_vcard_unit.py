"""Unit tests for the vCard (de)serialisation layer.

Covers the vobject-backed encoder / decoder without going
through Radicale. The vobject library has several
version-specific quirks around date / datetime values that
these tests pin down.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from genesis.carddav.vcard import (
    contact_from_vcard,
    contact_to_vcard,
    new_contact_uid,
)
from genesis.entities import Contact


def _contact(**overrides) -> Contact:
    base = {
        "uid": "c-abc123",
        "name": "Ada Lovelace",
        "created": datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        "modified": datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return Contact(**base)


# =============================================================================
# Encoding
# =============================================================================


class TestContactToVCard:
    def test_minimal_contact_produces_valid_vcard(self) -> None:
        text = contact_to_vcard(_contact())
        assert text.startswith("BEGIN:VCARD")
        assert "VERSION:3.0" in text
        assert "FN:Ada Lovelace" in text
        assert "UID:c-abc123" in text
        assert text.rstrip().endswith("END:VCARD")

    def test_optional_fields_omitted_when_none(self) -> None:
        text = contact_to_vcard(_contact())
        assert "NICKNAME" not in text
        assert "ORG" not in text
        assert "TITLE" not in text
        assert "BDAY" not in text

    def test_birthday_serialised_as_iso_date(self) -> None:
        text = contact_to_vcard(_contact(birthday=date(1815, 12, 10)))
        assert "BDAY:1815-12-10" in text

    def test_multi_value_fields_emit_repeated_properties(self) -> None:
        text = contact_to_vcard(
            _contact(
                emails=["a@x.com", "b@x.com"],
                phones=["+1", "+2"],
                urls=["https://a", "https://b"],
            )
        )
        assert text.count("EMAIL:") == 2
        assert text.count("TEL:") == 2
        assert text.count("URL:") == 2

    def test_structured_n_includes_all_components(self) -> None:
        text = contact_to_vcard(
            _contact(
                given_name="Ada",
                family_name="Lovelace",
                additional_names=["M"],
                name_prefix="Dr.",
                name_suffix="Jr.",
            )
        )
        assert "N:Lovelace;Ada;M;Dr.;Jr." in text

    def test_rev_serialised_when_modified_set(self) -> None:
        text = contact_to_vcard(_contact(modified=datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)))
        assert "REV:2026-01-02T12:00:00+00:00" in text


# =============================================================================
# Decoding
# =============================================================================


class TestContactFromVCard:
    def test_minimal_vcard_round_trips(self) -> None:
        original = _contact()
        text = contact_to_vcard(original)
        parsed = contact_from_vcard(text)
        assert parsed.uid == original.uid
        assert parsed.name == original.name
        assert parsed.given_name is None
        assert parsed.family_name is None

    def test_organisation_decodes_to_first_component(self) -> None:
        text = contact_to_vcard(_contact(organization="ACME Corp"))
        parsed = contact_from_vcard(text)
        assert parsed.organization == "ACME Corp"

    def test_birthday_round_trips_as_date(self) -> None:
        text = contact_to_vcard(_contact(birthday=date(1815, 12, 10)))
        parsed = contact_from_vcard(text)
        assert parsed.birthday == date(1815, 12, 10)

    def test_birthday_decodes_iso_string(self) -> None:
        text = "BEGIN:VCARD\nVERSION:3.0\nUID:c-test\nFN:Test\nN:;;;;\nBDAY:1815-12-10\nEND:VCARD\n"
        parsed = contact_from_vcard(text)
        assert parsed.birthday == date(1815, 12, 10)

    def test_multi_value_fields_round_trip(self) -> None:
        original = _contact(
            emails=["a@x.com", "b@x.com"],
            phones=["+1", "+2"],
            urls=["https://a", "https://b"],
            addresses=["10 St James Square, London"],
        )
        text = contact_to_vcard(original)
        parsed = contact_from_vcard(text)
        assert parsed.emails == ["a@x.com", "b@x.com"]
        assert parsed.phones == ["+1", "+2"]
        assert parsed.urls == ["https://a", "https://b"]
        assert parsed.addresses == ["10 St James Square, London"]

    def test_structured_n_round_trips(self) -> None:
        original = _contact(
            given_name="Ada",
            family_name="Lovelace",
            additional_names=["M"],
            name_prefix="Dr.",
            name_suffix="Jr.",
        )
        text = contact_to_vcard(original)
        parsed = contact_from_vcard(text)
        assert parsed.given_name == "Ada"
        assert parsed.family_name == "Lovelace"
        assert parsed.additional_names == ["M"]
        assert parsed.name_prefix == "Dr."
        assert parsed.name_suffix == "Jr."

    def test_rev_decoded_as_modified_and_created(self) -> None:
        text = "BEGIN:VCARD\nVERSION:3.0\nUID:c-test\nFN:Test\nN:;;;;\nREV:2026-01-02T12:00:00+00:00\nEND:VCARD\n"
        parsed = contact_from_vcard(text)
        assert parsed.modified is not None
        assert parsed.created is not None

    def test_missing_uid_yields_empty_string(self) -> None:
        text = "BEGIN:VCARD\nVERSION:3.0\nFN:Anon\nN:;;;;\nEND:VCARD\n"
        parsed = contact_from_vcard(text)
        assert parsed.uid == ""


# =============================================================================
# UIDs
# =============================================================================


class TestNewContactUid:
    def test_uid_uses_c_prefix(self) -> None:
        assert new_contact_uid().startswith("c-")

    def test_uids_are_unique(self) -> None:
        uids = {new_contact_uid() for _ in range(50)}
        assert len(uids) == 50
