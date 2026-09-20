"""UTC ISO timestamp helpers for the profile snapshot directory.

Filename form: ``YYYY-MM-DDTHHMMSSfffZ`` (no colons, no period —
lex-sortable, safe across filesystems). When a snapshot is read back
the same string is parsed back into a tz-aware :class:`datetime`.

Millisecond collisions get a ``-N`` counter appended before the
``.md`` / ``.meta.json`` suffix in :mod:`versions`; this module
only owns the timestamp generator and parser.
"""

from __future__ import annotations

from datetime import UTC, datetime

_FILENAME_FMT = "%Y-%m-%dT%H%M%S"


def now_iso_utc_millis(now: datetime | None = None) -> str:
    """Return the current UTC instant in filename form.

    Pure when given an explicit ``now``. Defaults to
    :func:`datetime.now` so callers don't have to thread clocks.
    """
    if now is None:
        now = datetime.now(tz=UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)
    return now.strftime(_FILENAME_FMT) + f"{now.microsecond // 1000:03d}Z"


def parse_iso_filename(s: str) -> datetime:
    """Parse a filename-form timestamp back to a tz-aware datetime.

    Accepts the bare timestamp or the timestamp with a trailing
    ``.md`` / ``.meta.json`` (handy when the caller passes a bare
    filename). Raises :class:`ValueError` for anything else.
    """
    if not isinstance(s, str) or not s:
        raise ValueError(f"Invalid ISO timestamp: {s!r}")
    for suffix in (".meta.json", ".md"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    if not s.endswith("Z"):
        raise ValueError(f"Timestamp is not UTC (no Z suffix): {s!r}")
    try:
        dt = datetime.strptime(s[:-1], _FILENAME_FMT + "%f")
    except ValueError as e:
        raise ValueError(f"Invalid ISO timestamp: {s!r}") from e
    return dt.replace(tzinfo=UTC)


def split_counter(filename_ts: str) -> tuple[str, int]:
    """Split a snapshot filename ts into ``(bare_ts, counter)``.

    ``2026-01-16T091500000Z`` -> ``("2026-01-16T091500000Z", 0)``
    ``2026-01-16T091500000Z-2`` -> ``("2026-01-16T091500000Z", 2)``

    The counter is 0 when absent. Used by :mod:`versions` to sort
    snapshots chronologically regardless of the counter suffix.
    """
    counter = 0
    if "-" in filename_ts:
        base, suffix = filename_ts.rsplit("-", 1)
        if suffix.isdigit():
            filename_ts = base
            counter = int(suffix)
    return filename_ts, counter


__all__ = [
    "now_iso_utc_millis",
    "parse_iso_filename",
    "split_counter",
]
