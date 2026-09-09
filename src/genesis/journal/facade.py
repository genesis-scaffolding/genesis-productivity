"""Facade for journal operations.

Provides access to journal entries stored as markdown files.
Each entry is keyed by a UID (UUID4 hex) and stored as ``{uid}.md``.

Identity rules:
- Daily entries are unique per date. ``save_entry`` rejects a daily
  save if another daily entry already exists for the same date.
- All other entry types (``weekly``, ``monthly``, ``yearly``, ``project``,
  ``misc``) have no uniqueness constraint — many may coexist on the
  same date.

Date semantics:
- ``Entry.date`` is the calendar day the entry represents (for daily)
  or the day it was created (for everything else). It is set at
  creation time and is not exposed as an editable field in the UI.
"""

from __future__ import annotations

import json
import re
from datetime import date as date_cls
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ..caldav.ical import new_uid
from ..tz import to_utc


class EntryType(StrEnum):
    """Journal entry type.

    Used to filter the journal list page. Values are stored verbatim in
    the markdown frontmatter. Changing a value is a
    backward-incompatible change for existing entries.
    """

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    PROJECT = "project"
    MISC = "misc"


class Entry(BaseModel):
    """A journal entry.

    Attributes:
        uid: UUID4 hex string. Primary identity. Auto-generated if not
            supplied on create.
        date: Calendar day. Set at creation. Not editable from the UI.
        title: Optional human title shown in the journal list and detail.
        type: Optional entry type used for filter chips and the daily
            uniqueness rule.
        content: Markdown body.
        created: When the file was created (from filesystem mtime).
        modified: When the file was last modified (from filesystem mtime).
    """

    uid: str = Field(default_factory=lambda: new_uid("j"))
    date: date_cls
    title: str | None = None
    type: EntryType | None = None
    content: str
    created: datetime | None = None
    modified: datetime | None = None

    model_config = {
        "extra": "ignore",
    }


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_LEGACY_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse(raw: str) -> tuple[dict, str]:
    """Split a file's raw text into frontmatter dict and body.

    Strips a single leading newline from the body so round-trips are
    clean: writing ``"\nbody"`` after the ``---`` separator and
    reading it back yields ``"body"``, not ``"\nbody"``.
    """
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}, raw
    if not isinstance(meta, dict):
        return {}, raw
    body = raw[match.end():]
    if body.startswith("\n"):
        body = body[1:]
    return meta, body


def _serialize(meta: dict, body: str) -> str:
    """Combine frontmatter dict and body into a file's raw text.

    String values that contain YAML-special characters (``:``, ``[``,
    ``&``, etc.) are JSON-quoted so the on-disk round-trip survives
    parsing. The ``date`` value is rendered unquoted as an ISO date
    to match the legacy on-disk format.
    """
    if not meta:
        return body
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, date_cls):
            lines.append(f"{key}: {value.isoformat()}")
        elif isinstance(value, str) and _needs_quoting(value):
            lines.append(f"{key}: {json.dumps(value)}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


def _needs_quoting(s: str) -> bool:
    """True if a string contains YAML-special characters and would
    not round-trip safely if written unquoted.
    """
    return any(c in s for c in ":#@&*!|>'\"%`[]{},?")


class JournalFacade:
    """Facade for journal entries stored as markdown files.

    Files are named ``{uid}.md``. The directory is created if it does
    not exist.
    """

    def __init__(self, journal_path: str | Path) -> None:
        self._journal_path = Path(journal_path)
        self._journal_path.mkdir(parents=True, exist_ok=True)

    def _entry_path(self, uid: str) -> Path:
        return self._journal_path / f"{uid}.md"

    def get_entry(self, uid: str) -> Entry | None:
        """Return the entry for the given UID, or None if not found.

        Files that fail to parse (malformed frontmatter, missing
        required fields) are silently skipped — the caller sees None
        rather than an exception, so a single bad file does not break
        the list.
        """
        path = self._entry_path(uid)
        if not path.exists():
            return None
        try:
            raw = path.read_text()
            meta, body = _parse(raw)
            stat = path.stat()
            type_str = meta.get("type")
            date_value = meta.get("date")
            # PyYAML parses an unquoted ISO date as a `datetime.date`,
            # not a string. Accept both forms.
            if isinstance(date_value, date_cls):
                entry_date = date_value
            elif isinstance(date_value, str):
                entry_date = date_cls.fromisoformat(date_value)
            else:
                entry_date = date_cls.fromtimestamp(stat.st_ctime)
            return Entry(
                uid=uid,
                date=entry_date,
                title=meta.get("title"),
                type=EntryType(type_str) if type_str else None,
                content=body,
                created=to_utc(datetime.fromtimestamp(stat.st_ctime)),
                modified=to_utc(datetime.fromtimestamp(stat.st_mtime)),
            )
        except (ValueError, KeyError, OSError, TypeError):
            return None

    def save_entry(self, entry: Entry) -> None:
        """Save an entry by UID.

        Raises ``ValueError`` if a daily entry with the same date
        already exists under a different UID.
        """
        if entry.type == EntryType.DAILY:
            existing = self.find_daily_for_date(entry.date)
            if existing is not None and existing.uid != entry.uid:
                raise ValueError(
                    f"A daily entry for {entry.date.isoformat()} already exists; "
                    "edit it instead"
                )
        path = self._entry_path(entry.uid)
        meta: dict = {"date": entry.date.isoformat()}
        if entry.type is not None:
            meta["type"] = entry.type.value
        if entry.title:
            meta["title"] = entry.title
        path.write_text(_serialize(meta, entry.content))

    def delete_entry(self, uid: str) -> bool:
        """Delete the entry for the given UID. Returns True if removed."""
        path = self._entry_path(uid)
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_entries(self) -> list[Entry]:
        """Return all parseable entries, newest first by file mtime.

        Files that fail to parse (legacy or malformed) are silently
        skipped. Legacy ``YYYY-MM-DD.md`` files from the previous
        filename scheme are also skipped — the user can delete them.
        """
        entries: list[Entry] = []
        for path in sorted(
            self._journal_path.glob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            if _LEGACY_DATE_PATTERN.match(path.stem):
                continue
            entry = self.get_entry(path.stem)
            if entry is not None:
                entries.append(entry)
        return entries

    def find_daily_for_date(self, target_date: date_cls) -> Entry | None:
        """Return the daily entry for ``target_date``, or None if none."""
        for entry in self.list_entries():
            if entry.type == EntryType.DAILY and entry.date == target_date:
                return entry
        return None
