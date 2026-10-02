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
        assert re.match(r"^j-[0-9a-z]{6}$", entry.uid)
        facade.save_entry(entry)
        loaded = facade.get_entry(entry.uid)
        assert loaded is not None
        assert loaded.uid == entry.uid

    def test_save_with_no_type_or_title_writes_minimal_frontmatter(self, tmp_path: Path) -> None:
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
        (tmp_path / f"{bad.uid}.md").write_text("---\ndate: 2026-09-05\ntype: garbled\n---\n\nbody\n")
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

    def test_periodic_types_have_uniqueness_within_period(self, tmp_path: Path) -> None:
        # Two weeklies filed for the same ISO week — second must fail.
        facade = JournalFacade(tmp_path)
        a = Entry(
            date=date(2026, 9, 5),  # Saturday
            type=EntryType.WEEKLY,
            content="a",
        )
        facade.save_entry(a)

        b = Entry(
            date=date(2026, 9, 6),  # Sunday of same week
            type=EntryType.WEEKLY,
            content="b",
        )
        with pytest.raises(ValueError, match="weekly entry for 2026-08-31"):
            facade.save_entry(b)

        # Different week — succeeds
        c = Entry(
            date=date(2026, 9, 12),  # Saturday of next week
            type=EntryType.WEEKLY,
            content="c",
        )
        facade.save_entry(c)
        assert len(facade.list_entries()) == 2

    def test_project_and_misc_types_have_no_uniqueness_constraint(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        for i in range(3):
            entry = Entry(
                date=date(2026, 9, 5),
                type=EntryType.PROJECT,
                content=f"p{i}",
            )
            facade.save_entry(entry)
        for i in range(3):
            entry = Entry(
                date=date(2026, 9, 5),
                type=EntryType.MISC,
                content=f"m{i}",
            )
            facade.save_entry(entry)
        assert len(facade.list_entries()) == 6

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


class TestReferenceDate:
    """Server-side period anchoring for periodic entries."""

    def test_daily_save_stores_reference_date_equal_to_date(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        entry = Entry(date=date(2026, 9, 5), type=EntryType.DAILY, content="x")
        facade.save_entry(entry)
        loaded = facade.get_entry(entry.uid)
        assert loaded is not None
        assert loaded.reference_date == date(2026, 9, 5)

    def test_weekly_save_stores_reference_date_as_monday(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        # 2026-09-05 is a Saturday; Monday of that ISO week is 2026-08-31.
        entry = Entry(date=date(2026, 9, 5), type=EntryType.WEEKLY, content="x")
        facade.save_entry(entry)
        loaded = facade.get_entry(entry.uid)
        assert loaded is not None
        assert loaded.reference_date == date(2026, 8, 31)

    def test_monthly_save_stores_reference_date_as_first(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        entry = Entry(date=date(2026, 9, 15), type=EntryType.MONTHLY, content="x")
        facade.save_entry(entry)
        loaded = facade.get_entry(entry.uid)
        assert loaded is not None
        assert loaded.reference_date == date(2026, 9, 1)

    def test_yearly_save_stores_reference_date_as_jan_1(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        entry = Entry(date=date(2026, 9, 5), type=EntryType.YEARLY, content="x")
        facade.save_entry(entry)
        loaded = facade.get_entry(entry.uid)
        assert loaded is not None
        assert loaded.reference_date == date(2026, 1, 1)

    def test_briefing_save_stores_reference_date_equal_to_date(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        entry = Entry(date=date(2026, 9, 5), type=EntryType.BRIEFING, content="b")
        facade.save_entry(entry)
        loaded = facade.get_entry(entry.uid)
        assert loaded is not None
        assert loaded.reference_date == date(2026, 9, 5)
        assert facade.find_for_period(EntryType.BRIEFING, date(2026, 9, 5)) is not None

    def test_briefing_uniqueness_blocks_second_save_same_day(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        a = Entry(date=date(2026, 9, 5), type=EntryType.BRIEFING, content="a")
        facade.save_entry(a)
        b = Entry(date=date(2026, 9, 5), type=EntryType.BRIEFING, content="b")
        with pytest.raises(ValueError, match="briefing entry for 2026-09-05"):
            facade.save_entry(b)
        # A daily entry for the same day is unaffected
        d = Entry(date=date(2026, 9, 5), type=EntryType.DAILY, content="d")
        facade.save_entry(d)

    def test_project_and_misc_save_omit_reference_date(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        for type_ in (EntryType.PROJECT, EntryType.MISC):
            entry = Entry(date=date(2026, 9, 5), type=type_, content="x")
            facade.save_entry(entry)
            loaded = facade.get_entry(entry.uid)
            assert loaded is not None
            assert loaded.reference_date is None

    def test_explicit_reference_date_accepted_when_matches_normalised(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        entry = Entry(
            date=date(2026, 9, 5),
            type=EntryType.WEEKLY,
            reference_date=date(2026, 8, 31),  # Monday
            content="x",
        )
        facade.save_entry(entry)
        loaded = facade.get_entry(entry.uid)
        assert loaded is not None
        assert loaded.reference_date == date(2026, 8, 31)

    def test_explicit_reference_date_rejected_when_mismatched(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        entry = Entry(
            date=date(2026, 9, 5),
            type=EntryType.WEEKLY,
            reference_date=date(2026, 9, 1),  # Wednesday, not Monday
            content="x",
        )
        with pytest.raises(ValueError, match="does not match"):
            facade.save_entry(entry)

    def test_weekly_uniqueness_blocks_second_save_in_same_week(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        a = Entry(date=date(2026, 9, 5), type=EntryType.WEEKLY, content="a")
        facade.save_entry(a)
        # Sunday of the same ISO week — different date, same period.
        b = Entry(date=date(2026, 9, 6), type=EntryType.WEEKLY, content="b")
        with pytest.raises(ValueError, match="weekly entry for 2026-08-31"):
            facade.save_entry(b)

    def test_weekly_different_weeks_both_succeed(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        # Week of Aug 31
        a = Entry(date=date(2026, 9, 5), type=EntryType.WEEKLY, content="a")
        # Week of Sep 7
        b = Entry(date=date(2026, 9, 12), type=EntryType.WEEKLY, content="b")
        facade.save_entry(a)
        facade.save_entry(b)
        assert len(facade.list_entries()) == 2

    def test_monthly_uniqueness_blocks_second_save_in_same_month(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        a = Entry(date=date(2026, 9, 5), type=EntryType.MONTHLY, content="a")
        facade.save_entry(a)
        b = Entry(date=date(2026, 9, 25), type=EntryType.MONTHLY, content="b")
        with pytest.raises(ValueError, match="monthly entry for 2026-09-01"):
            facade.save_entry(b)

    def test_monthly_different_months_both_succeed(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        a = Entry(date=date(2026, 9, 5), type=EntryType.MONTHLY, content="a")
        b = Entry(date=date(2026, 10, 5), type=EntryType.MONTHLY, content="b")
        facade.save_entry(a)
        facade.save_entry(b)
        assert len(facade.list_entries()) == 2

    def test_yearly_uniqueness_blocks_second_save_in_same_year(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        a = Entry(date=date(2026, 1, 5), type=EntryType.YEARLY, content="a")
        facade.save_entry(a)
        b = Entry(date=date(2026, 12, 31), type=EntryType.YEARLY, content="b")
        with pytest.raises(ValueError, match="yearly entry for 2026-01-01"):
            facade.save_entry(b)

    def test_yearly_different_years_both_succeed(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        a = Entry(date=date(2026, 9, 5), type=EntryType.YEARLY, content="a")
        b = Entry(date=date(2027, 9, 5), type=EntryType.YEARLY, content="b")
        facade.save_entry(a)
        facade.save_entry(b)
        assert len(facade.list_entries()) == 2

    def test_find_for_period_weekly(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        w = Entry(date=date(2026, 9, 5), type=EntryType.WEEKLY, content="w")
        facade.save_entry(w)
        # Any day in the same ISO week finds it.
        hit = facade.find_for_period(EntryType.WEEKLY, date(2026, 9, 6))
        assert hit is not None and hit.uid == w.uid
        hit = facade.find_for_period(EntryType.WEEKLY, date(2026, 9, 1))
        assert hit is not None and hit.uid == w.uid
        # Day in a different week returns None.
        assert facade.find_for_period(EntryType.WEEKLY, date(2026, 9, 7)) is None
        assert facade.find_for_period(EntryType.WEEKLY, date(2026, 9, 12)) is None

    def test_find_for_period_monthly(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        m = Entry(date=date(2026, 9, 5), type=EntryType.MONTHLY, content="m")
        facade.save_entry(m)
        hit = facade.find_for_period(EntryType.MONTHLY, date(2026, 9, 1))
        assert hit is not None and hit.uid == m.uid
        hit = facade.find_for_period(EntryType.MONTHLY, date(2026, 9, 30))
        assert hit is not None and hit.uid == m.uid
        assert facade.find_for_period(EntryType.MONTHLY, date(2026, 10, 1)) is None

    def test_find_for_period_yearly(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        y = Entry(date=date(2026, 9, 5), type=EntryType.YEARLY, content="y")
        facade.save_entry(y)
        hit = facade.find_for_period(EntryType.YEARLY, date(2026, 1, 1))
        assert hit is not None and hit.uid == y.uid
        hit = facade.find_for_period(EntryType.YEARLY, date(2026, 12, 31))
        assert hit is not None and hit.uid == y.uid
        assert facade.find_for_period(EntryType.YEARLY, date(2027, 1, 1)) is None

    def test_find_for_period_project_returns_none(self, tmp_path: Path) -> None:
        facade = JournalFacade(tmp_path)
        p = Entry(date=date(2026, 9, 5), type=EntryType.PROJECT, content="p")
        facade.save_entry(p)
        assert facade.find_for_period(EntryType.PROJECT, date(2026, 9, 5)) is None
        assert facade.find_for_period(EntryType.MISC, date(2026, 9, 5)) is None

    def test_legacy_daily_defaults_reference_date_to_date(self, tmp_path: Path) -> None:
        # A file written before ADR-017 has no ``reference-date`` line.
        # Daily entries should be readable and default their
        # ``reference_date`` to ``date`` so uniqueness still works.
        path = tmp_path / "j-legacy01.md"
        path.write_text("---\ndate: 2026-09-05\ntype: daily\n---\n\nbody\n")
        facade = JournalFacade(tmp_path)
        loaded = facade.get_entry("j-legacy01")
        assert loaded is not None
        assert loaded.reference_date == date(2026, 9, 5)

    def test_legacy_periodic_defaults_reference_date_to_none(self, tmp_path: Path) -> None:
        path = tmp_path / "j-legacy02.md"
        path.write_text("---\ndate: 2026-09-05\ntype: weekly\n---\n\nbody\n")
        facade = JournalFacade(tmp_path)
        loaded = facade.get_entry("j-legacy02")
        assert loaded is not None
        assert loaded.reference_date is None

    def test_repair_reference_dates_populates_periodic_entries(self, tmp_path: Path) -> None:
        # Legacy periodic files without ``reference-date``.
        (tmp_path / "j-legacy01.md").write_text("---\ndate: 2026-09-05\ntype: weekly\n---\n\nweekly body\n")
        (tmp_path / "j-legacy02.md").write_text("---\ndate: 2026-09-15\ntype: monthly\n---\n\nmonthly body\n")
        (tmp_path / "j-legacy03.md").write_text("---\ndate: 2026-09-15\ntype: yearly\n---\n\nyearly body\n")
        facade = JournalFacade(tmp_path)
        report = facade.repair_reference_dates()
        assert report.repaired == 3
        assert report.failures == []
        loaded = facade.get_entry("j-legacy01")
        assert loaded is not None
        assert loaded.reference_date == date(2026, 8, 31)  # Monday
        loaded = facade.get_entry("j-legacy02")
        assert loaded is not None
        assert loaded.reference_date == date(2026, 9, 1)
        loaded = facade.get_entry("j-legacy03")
        assert loaded is not None
        assert loaded.reference_date == date(2026, 1, 1)

    def test_repair_reference_dates_is_idempotent(self, tmp_path: Path) -> None:
        (tmp_path / "j-legacy01.md").write_text("---\ndate: 2026-09-05\ntype: weekly\n---\n\nbody\n")
        facade = JournalFacade(tmp_path)
        first = facade.repair_reference_dates()
        second = facade.repair_reference_dates()
        assert first.repaired == 1
        assert second.repaired == 0

    def test_repair_reference_dates_reports_collisions(self, tmp_path: Path) -> None:
        # Two legacy weeklies filed under different days of the same
        # ISO week collide once ``reference-date`` is populated.
        (tmp_path / "j-legacy01.md").write_text("---\ndate: 2026-09-05\ntype: weekly\n---\n\nfirst\n")
        (tmp_path / "j-legacy02.md").write_text("---\ndate: 2026-09-06\ntype: weekly\n---\n\nsecond\n")
        facade = JournalFacade(tmp_path)
        report = facade.repair_reference_dates()
        # First one wins, second is reported as a failure.
        assert report.repaired == 1
        assert len(report.failures) == 1
        assert "j-legacy02" in report.failures[0][0]
