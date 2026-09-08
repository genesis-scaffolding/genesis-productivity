"""In-memory mock for ``genesis.journal.facade.JournalFacade``.

The API tests fall into two buckets: round-trip tests that exercise
iCal/markdown persistence, and pure API tests that exercise filter
arithmetic and pagination. Doing the latter against real Radicale
costs ~2 seconds per test for the subprocess startup.

This module gives us a drop-in JournalFacade substitute that holds
entries in a ``dict[uid, Entry]`` instead of writing files. The
public surface mirrors the real facade:

- ``get_entry(uid) -> Entry | None``
- ``save_entry(entry)`` (raises ``ValueError`` on daily-collision)
- ``delete_entry(uid) -> bool``
- ``list_entries() -> list[Entry]``
- ``find_daily_for_date(target_date) -> Entry | None``

It does NOT round-trip content through YAML frontmatter; the API
router applies the ``> Project: {name}`` footer to the body before
calling ``save_entry`` and strips it on read. The mock just
stores and returns the content as-is, which matches the API
contract.
"""

from __future__ import annotations

from datetime import date as date_cls

from genesis.journal.facade import Entry, EntryType


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
        """Save the entry. Raises on daily-collision (matches real)."""
        if entry.type == EntryType.DAILY:
            existing = self.find_daily_for_date(entry.date)
            if existing is not None and existing.uid != entry.uid:
                raise ValueError(
                    f"A daily entry for {entry.date.isoformat()} already exists; "
                    "edit it instead"
                )
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
        for entry in self.list_entries():
            if entry.type == EntryType.DAILY and entry.date == target_date:
                return entry
        return None


__all__ = ["InMemoryJournalFacade"]
