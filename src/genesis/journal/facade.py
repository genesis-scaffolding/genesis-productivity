"""Facade for journal operations.

Provides access to journal entries stored as markdown files.
Each entry is keyed by a UID (UUID4 hex) and stored as ``{uid}.md``.

Identity rules:
- Daily / weekly / monthly / yearly entries are unique within their
  period. ``save_entry`` rejects a save if another entry of the same
  type already owns the period (same date for daily, same ISO week
  for weekly, same month for monthly, same year for yearly).
- ``project`` and ``misc`` entries have no uniqueness constraint —
  many may coexist.

Date semantics (see ADR-017):
- ``Entry.date`` is the creation day for the entry. It is set when
  the entry is first saved and is not editable via the API.
- ``Entry.reference_date`` is the period anchor: the calendar day for
  a daily entry, Monday of that ISO week for a weekly entry, the
  1st of the month for a monthly entry, January 1 for a yearly
  entry, and ``None`` for project / misc / untyped entries. The
  server computes it from ``date`` + ``type`` on save. The PATCH
  endpoint does not accept changes to either field; clients must
  delete and re-create to relocate an entry to a different period.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime, timedelta
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


# Entry types that have a period anchor and therefore participate in
# uniqueness checks. ``project`` and ``misc`` are free-form notes.
_PERIODIC_TYPES: frozenset[EntryType] = frozenset(
    {EntryType.DAILY, EntryType.WEEKLY, EntryType.MONTHLY, EntryType.YEARLY}
)


class Entry(BaseModel):
    """A journal entry.

    Attributes:
        uid: UUID4 hex string. Primary identity. Auto-generated if not
            supplied on create.
        date: Creation day. Set at creation; not editable from the API.
        reference_date: Period anchor (Monday for weekly, 1st of month
            for monthly, January 1 for yearly, ``date`` for daily,
            ``None`` for project / misc / untyped). Server-computed on
            save; not editable from the API.
        title: Optional human title shown in the journal list and detail.
        type: Optional entry type used for filter chips and the periodic
            uniqueness rule.
        content: Markdown body.
        created: When the file was created (from filesystem mtime).
        modified: When the file was last modified (from filesystem mtime).
    """

    uid: str = Field(default_factory=lambda: new_uid("j"))
    date: date_cls
    reference_date: date_cls | None = None
    title: str | None = None
    type: EntryType | None = None
    content: str
    created: datetime | None = None
    modified: datetime | None = None

    model_config = {
        "extra": "ignore",
    }


def _normalize_reference_date(
    entry_type: EntryType | None,
    on: date_cls,
) -> date_cls | None:
    """Return the period anchor for ``entry_type`` applied to ``on``.

    - daily   -> ``on`` (the day itself)
    - weekly  -> Monday of ``on``'s ISO week
    - monthly -> 1st of ``on``'s month
    - yearly  -> January 1 of ``on``'s year
    - project, misc, None -> ``None`` (no period anchor)
    """
    if entry_type is None:
        return None
    if entry_type == EntryType.DAILY:
        return on
    if entry_type == EntryType.WEEKLY:
        return on - timedelta(days=on.isoweekday() - 1)
    if entry_type == EntryType.MONTHLY:
        return on.replace(day=1)
    if entry_type == EntryType.YEARLY:
        return on.replace(month=1, day=1)
    return None


@dataclass
class RepairReport:
    """Outcome of :meth:`JournalFacade.repair_reference_dates`.

    Attributes:
        repaired: Number of entries whose ``reference-date`` was
            successfully populated.
        failures: ``(uid, error_message)`` pairs for entries that
            could not be repaired (e.g. a duplicate emerged after
            normalising). The migration never raises on per-entry
            failures — they are reported here so the caller can
            decide how to handle them.
    """

    repaired: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


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
            entry_type = EntryType(type_str) if type_str else None
            ref_value = meta.get("reference-date")
            if isinstance(ref_value, date_cls):
                entry_ref = ref_value
            elif isinstance(ref_value, str):
                entry_ref = date_cls.fromisoformat(ref_value)
            else:
                # Legacy file written before ADR-017 — no
                # ``reference-date`` line. Daily entries default to
                # ``date`` (which is already the calendar day for
                # daily entries). Periodic entries default to
                # ``None`` until :meth:`repair_reference_dates`
                # brings them up to date.
                entry_ref = entry_date if entry_type == EntryType.DAILY else None
            return Entry(
                uid=uid,
                date=entry_date,
                reference_date=entry_ref,
                title=meta.get("title"),
                type=entry_type,
                content=body,
                created=to_utc(datetime.fromtimestamp(stat.st_ctime)),
                modified=to_utc(datetime.fromtimestamp(stat.st_mtime)),
            )
        except (ValueError, KeyError, OSError, TypeError):
            return None

    def save_entry(self, entry: Entry) -> None:
        """Save an entry by UID.

        Server-side behaviour:

        - Computes the period anchor from ``entry.type`` and ``entry.date``.
          If the caller supplied ``entry.reference_date`` explicitly, it
          is validated against the anchor — daily must equal ``date``,
          weekly must equal Monday of that ISO week, monthly must equal
          the 1st, yearly must equal January 1. A mismatch raises
          ``ValueError``.
        - For daily / weekly / monthly / yearly entries, rejects the save
          with ``ValueError`` if another entry of the same type already
          owns the period.
        - Persists ``reference-date`` to the frontmatter whenever it is
          not ``None``.

        ``project`` and ``misc`` entries skip the uniqueness check; they
        are free-form notes, not periodic anchors.
        """
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

        path = self._entry_path(entry.uid)
        meta: dict = {"date": entry.date.isoformat()}
        if effective_ref is not None:
            meta["reference-date"] = effective_ref.isoformat()
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
        """Return the daily entry for ``target_date``, or None if none.

        Thin wrapper around :meth:`find_for_period` kept for callers
        that want a daily-specific name.
        """
        return self.find_for_period(EntryType.DAILY, target_date)

    def find_for_period(
        self, entry_type: EntryType, on_or_in: date_cls
    ) -> Entry | None:
        """Return the periodic entry for ``entry_type`` covering ``on_or_in``.

        The reference date is normalised server-side: any day in a week
        for ``weekly``, any day in a month for ``monthly``, any day in
        a year for ``yearly``. Daily accepts the date as-is. Project
        and misc have no period anchor and always return ``None``.
        """
        normalized = _normalize_reference_date(entry_type, on_or_in)
        if normalized is None:
            return None
        for entry in self.list_entries():
            if entry.type == entry_type and entry.reference_date == normalized:
                return entry
        return None

    def repair_reference_dates(self) -> RepairReport:
        """One-shot migration for periodic entries missing ``reference-date``.

        Walks the journal directory. For each weekly / monthly / yearly
        entry whose on-disk ``reference-date`` is missing, computes the
        period anchor from ``(type, date)`` and re-saves the file. The
        re-save goes through :meth:`save_entry`, so any duplicate that
        emerges after normalisation is rejected and reported in
        ``failures`` rather than raised.

        Idempotent: re-running on already-repaired entries is a no-op.

        Legacy daily entries and untyped / project / misc entries are
        skipped — daily entries have ``reference-date == date`` which
        is filled in on read; the others have no period anchor.
        """
        report = RepairReport()
        for path in sorted(self._journal_path.glob("*.md")):
            if _LEGACY_DATE_PATTERN.match(path.stem):
                continue
            entry = self.get_entry(path.stem)
            if entry is None:
                continue
            if entry.type not in _PERIODIC_TYPES:
                continue
            if entry.reference_date is not None:
                continue
            entry.reference_date = _normalize_reference_date(entry.type, entry.date)
            try:
                self.save_entry(entry)
                report.repaired += 1
            except ValueError as exc:
                report.failures.append((entry.uid, str(exc)))
        return report
