"""In-memory mock for ``genesis.journal.facade.JournalFacade``.

The API tests fall into two buckets: round-trip tests that exercise
iCal/markdown persistence, and pure API tests that exercise filter
arithmetic and pagination. Doing the latter against real Radicale
costs ~2 seconds per test for the subprocess startup.

This module gives us a drop-in JournalFacade substitute that holds
entries in a ``dict[uid, Entry]`` instead of writing files. The
public surface mirrors the real facade:

- ``get_entry(uid) -> Entry | None``
- ``save_entry(entry)`` (raises ``ValueError`` on periodic-collision)
- ``delete_entry(uid) -> bool``
- ``list_entries() -> list[Entry]``
- ``find_daily_for_date(target_date) -> Entry | None``
- ``find_for_period(entry_type, on_or_in) -> Entry | None``
- ``repair_reference_dates() -> RepairReport``

It does NOT round-trip content through YAML frontmatter; the API
router applies the ``> Project: {name}`` footer to the body before
calling ``save_entry`` and strips it on read. The mock just
stores and returns the content as-is, which matches the API
contract.
"""

from __future__ import annotations

from datetime import date as date_cls

from genesis.journal.facade import (
    Entry,
    EntryType,
    RepairReport,
    _normalize_reference_date,
)


class InMemoryJournalFacade:
    """Stand-in for ``JournalFacade`` that holds entries in memory.

    ``__init__`` accepts the same ``journal_path`` argument as the
    real facade but ignores it — there is no on-disk state.
    """

    def __init__(self, journal_path: str | object = "") -> None:
        self._entries: dict[str, Entry] = {}
        self._saved_order: list[str] = []
        # Tracks when each uid was last touched so list_entries can
        # produce a stable newest-first ordering without reading mtimes.
        self._counter: int = 0

    def _next(self) -> int:
        self._counter += 1
        return self._counter

    def get_entry(self, uid: str) -> Entry | None:
        return self._entries.get(uid)

    def save_entry(self, entry: Entry) -> None:
        """Save the entry. Raises on periodic-collision (matches real)."""
        normalized_ref = _normalize_reference_date(entry.type, entry.date)
        if entry.type in _PERIODIC_TYPES:
            assert entry.type is not None  # for type checker
            assert normalized_ref is not None  # periodic types always have an anchor
            if (
                entry.reference_date is not None
                and entry.reference_date != normalized_ref
            ):
                raise ValueError(
                    f"reference_date {entry.reference_date.isoformat()} does not "
                    f"match the normalised anchor {normalized_ref.isoformat()} "
                    f"for type {entry.type.value}",
                )
            effective_ref = (
                entry.reference_date
                if entry.reference_date is not None
                else normalized_ref
            )
            if effective_ref is not None:
                existing = self.find_for_period(entry.type, effective_ref)
                if existing is not None and existing.uid != entry.uid:
                    raise ValueError(
                        f"A {entry.type.value} entry for "
                        f"{effective_ref.isoformat()} already exists; "
                        "edit it instead",
                    )
        else:
            effective_ref = entry.reference_date
        # Persist the normalised reference_date alongside the caller's
        # input so list_entries can filter on it like the real facade.
        if entry.reference_date is None and normalized_ref is not None:
            entry = entry.model_copy(update={"reference_date": normalized_ref})
        if entry.uid not in self._entries:
            self._saved_order.append(entry.uid)
        self._entries[entry.uid] = entry

    def delete_entry(self, uid: str) -> bool:
        if uid not in self._entries:
            return False
        del self._entries[uid]
        self._saved_order = [u for u in self._saved_order if u != uid]
        return True

    def list_entries(self) -> list[Entry]:
        """Return all entries in reverse-insertion order (newest first)."""
        return [self._entries[uid] for uid in reversed(self._saved_order)]

    def find_daily_for_date(self, target_date: date_cls) -> Entry | None:
        return self.find_for_period(EntryType.DAILY, target_date)

    def find_for_period(
        self, entry_type: EntryType, on_or_in: date_cls
    ) -> Entry | None:
        normalized = _normalize_reference_date(entry_type, on_or_in)
        if normalized is None:
            return None
        for entry in self.list_entries():
            if entry.type == entry_type and entry.reference_date == normalized:
                return entry
        return None

    def repair_reference_dates(self) -> RepairReport:
        report = RepairReport()
        for entry in list(self.list_entries()):
            if entry.type not in _PERIODIC_TYPES:
                continue
            if entry.reference_date is not None:
                continue
            try:
                self.save_entry(entry)
                report.repaired += 1
            except ValueError as exc:
                report.failures.append((entry.uid, str(exc)))
        return report


_PERIODIC_TYPES = frozenset(
    {EntryType.DAILY, EntryType.WEEKLY, EntryType.MONTHLY, EntryType.YEARLY}
)


__all__ = ["InMemoryJournalFacade"]
