"""Facade for journal operations.

Provides access to journal entries stored as markdown files.
Entries are organized by date: YYYY-MM-DD.md
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel


class Entry(BaseModel):
    """A journal entry."""

    date: date
    content: str
    created: datetime | None = None
    modified: datetime | None = None


class JournalFacade:
    """Facade for journal entries stored as markdown files.

    Entries are stored at <journal_path>/YYYY-MM-DD.md
    The directory is created if it does not exist.
    """

    def __init__(self, journal_path: str | Path) -> None:
        self._journal_path = Path(journal_path)
        self._journal_path.mkdir(parents=True, exist_ok=True)

    def _entry_path(self, entry_date: date) -> Path:
        """Return the path for a given date's entry."""
        return self._journal_path / f"{entry_date.isoformat()}.md"

    def get_entry(self, entry_date: date) -> Entry | None:
        """Return the entry for a given date, or None if not found."""
        path = self._entry_path(entry_date)
        if not path.exists():
            return None
        content = path.read_text()
        stat = path.stat()
        return Entry(
            date=entry_date,
            content=content,
            created=datetime.fromtimestamp(stat.st_ctime),
            modified=datetime.fromtimestamp(stat.st_mtime),
        )

    def save_entry(self, entry: Entry) -> None:
        """Save an entry for the given date."""
        path = self._entry_path(entry.date)
        path.write_text(entry.content)

    def list_entries(self) -> list[Entry]:
        """Return all journal entries sorted by date."""
        entries: list[Entry] = []
        for path in sorted(self._journal_path.glob("*.md")):
            try:
                entry_date = date.fromisoformat(path.stem)
                entry = self.get_entry(entry_date)
                if entry is not None:
                    entries.append(entry)
            except ValueError:
                continue
        return entries
