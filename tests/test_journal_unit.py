"""Unit tests for the journal facade (UID, frontmatter, daily uniqueness)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from genesis.journal.facade import Entry, EntryType, JournalFacade


class TestParseFrontmatter:
    """Tests for the frontmatter parser."""

    def test_no_frontmatter_returns_empty_meta_and_full_body(self) -> None:
        from genesis.journal.facade import _parse

        meta, body = _parse("Hello world\n")
        assert meta == {}
        assert body == "Hello world\n"

    def test_full_frontmatter_parses(self) -> None:
        from datetime import date as date_cls

        from genesis.journal.facade import _parse

        raw = "---\ndate: 2026-09-05\ntype: daily\ntitle: Hi\n---\n\nBody text\n"
        meta, body = _parse(raw)
        # PyYAML parses unquoted ISO dates as datetime.date objects.
        assert meta == {"date": date_cls(2026, 9, 5), "type": "daily", "title": "Hi"}
        assert body == "Body text\n"

    def test_malformed_frontmatter_falls_back_to_body(self) -> None:
        from genesis.journal.facade import _parse

        raw = "---\ntype: daily\nbody without closing fence"
        meta, body = _parse(raw)
        assert meta == {}
        assert body == raw


class TestSerialize:
    """Tests for the frontmatter serializer."""

    def test_empty_meta_writes_body_only(self) -> None:
        from genesis.journal.facade import _serialize

        assert _serialize({}, "body text\n") == "body text\n"

    def test_meta_writes_frontmatter_block(self) -> None:
        from genesis.journal.facade import _serialize

        out = _serialize({"date": "2026-09-05", "type": "daily", "title": "Hi"}, "body\n")
        assert out.startswith("---\n")
        assert "date: 2026-09-05" in out
        assert "type: daily" in out
        assert "title: Hi" in out
        assert out.endswith("\nbody\n")


class TestJournalFacade:
    """End-to-end tests against a temporary journal directory."""

    def test_save_then_load_round_trips_full_entry(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        entry = Entry(
            date=date(2026, 9, 5),
            title="Weight Lifting Session",
            type=EntryType.DAILY,
            content="Leg press 5x5\n",
        )
        facade.save_entry(entry)

        loaded = facade.get_entry(entry.uid)
        assert loaded is not None
        assert loaded.uid == entry.uid
        assert loaded.title == "Weight Lifting Session"
        assert loaded.type == EntryType.DAILY
        assert loaded.content == "Leg press 5x5\n"
        assert loaded.date == date(2026, 9, 5)

    def test_uid_is_generated_on_save(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        entry = Entry(date=date(2026, 9, 5), content="x")
        assert entry.uid  # factory ran on construction
        assert re.match(r"^[0-9a-f]{32}$", entry.uid)
        facade.save_entry(entry)
        loaded = facade.get_entry(entry.uid)
        assert loaded is not None
        assert loaded.uid == entry.uid

    def test_save_with_no_type_or_title_writes_minimal_frontmatter(
        self, tmp_path: Path
    ) -> None:
        facade = JournalFacade(tmp_path)
        entry = Entry(date=date(2026, 9, 5), content="Just a body\n")
        facade.save_entry(entry)

        raw = (tmp_path / f"{entry.uid}.md").read_text()
        assert raw.startswith("---\n")
        assert "date: 2026-09-05" in raw
        # No `type:` or `title:` lines
        assert "type:" not in raw
        assert "title:" not in raw
        assert raw.endswith("\nJust a body\n")

        loaded = facade.get_entry(entry.uid)
        assert loaded is not None
        assert loaded.title is None
        assert loaded.type is None
        assert loaded.content == "Just a body\n"

    def test_list_entries_returns_newest_first(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        for d in [date(2026, 9, 1), date(2026, 9, 5), date(2026, 9, 3)]:
            entry = Entry(date=d, content=f"body for {d}\n")
            facade.save_entry(entry)
        listed = facade.list_entries()
        # Newest first by file mtime — saves happen in 1, 5, 3 order,
        # so the listing is 3, 5, 1.
        assert [e.date for e in listed] == [
            date(2026, 9, 3),
            date(2026, 9, 5),
            date(2026, 9, 1),
        ]

    def test_unknown_type_string_skips_entry(self, tmp_path: Path) -> None:
        # A type value the enum doesn't know about should make the
        # entry unloadable — easier to spot bad data than silent
        # defaults.
        bad = Entry(date=date(2026, 9, 5), content="body\n", type=EntryType.DAILY)
        facade = JournalFacade(tmp_path)
        facade.save_entry(bad)
        # Tamper with the file to inject a bogus type
        (tmp_path / f"{bad.uid}.md").write_text(
            "---\ndate: 2026-09-05\ntype: garbled\n---\n\nbody\n"
        )
        assert facade.get_entry(bad.uid) is None
        assert facade.list_entries() == []

    def test_daily_uniqueness_blocks_second_save(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        a = Entry(date=date(2026, 9, 5), type=EntryType.DAILY, content="a")
        facade.save_entry(a)

        b = Entry(date=date(2026, 9, 5), type=EntryType.DAILY, content="b")
        with pytest.raises(ValueError, match="daily entry for 2026-09-05"):
            facade.save_entry(b)

        # Edit of the same entry (same uid) is fine
        a.content = "a edited"
        facade.save_entry(a)
        loaded = facade.get_entry(a.uid)
        assert loaded is not None
        assert loaded.content == "a edited"

    def test_non_daily_types_have_no_uniqueness_constraint(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        for i in range(3):
            entry = Entry(
                date=date(2026, 9, 5), type=EntryType.WEEKLY, content=f"w{i}"
            )
            facade.save_entry(entry)
        assert len(facade.list_entries()) == 3

    def test_different_dates_can_have_different_dailies(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        a = Entry(date=date(2026, 9, 5), type=EntryType.DAILY, content="a")
        b = Entry(date=date(2026, 9, 6), type=EntryType.DAILY, content="b")
        facade.save_entry(a)
        facade.save_entry(b)  # different date, no collision
        assert len(facade.list_entries()) == 2

    def test_find_daily_for_date(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        a = Entry(date=date(2026, 9, 5), type=EntryType.DAILY, content="a")
        w = Entry(date=date(2026, 9, 5), type=EntryType.WEEKLY, content="w")
        b = Entry(date=date(2026, 9, 6), type=EntryType.DAILY, content="b")
        facade.save_entry(a)
        facade.save_entry(w)
        facade.save_entry(b)

        hit = facade.find_daily_for_date(date(2026, 9, 5))
        assert hit is not None
        assert hit.uid == a.uid

        assert facade.find_daily_for_date(date(2026, 9, 7)) is None

    def test_delete_entry_by_uid(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        entry = Entry(date=date(2026, 9, 5), content="x")
        facade.save_entry(entry)
        assert facade.delete_entry(entry.uid) is True
        assert facade.get_entry(entry.uid) is None
        assert facade.delete_entry(entry.uid) is False

    def test_legacy_date_filename_is_skipped(self, tmp_path: Path) -> None:
        # A file from the previous model (no uid, named YYYY-MM-DD.md)
        # is silently skipped by list_entries. The user can delete it.
        (tmp_path / "2026-09-06.md").write_text("Old body text\n")
        facade = JournalFacade(tmp_path)
        assert facade.list_entries() == []
        # The legacy file is still on disk — we do not auto-migrate.
        assert (tmp_path / "2026-09-06.md").exists()
