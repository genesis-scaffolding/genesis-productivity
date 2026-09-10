"""One-shot migration CLI: backfill missing time log categories.

Existing time log entries written before this feature was added
have no ``X-CATEGORY`` on the VTODO. The CalDAV layer reads them
as ``TimeLogCategory.OTHER``, which is safe but unhelpful.

Run::

    uv run python -m genesis.category.migration [--dry-run]

The CLI iterates every time log entry, finds those without a
category, runs inference (with the rest of the corpus as history),
and writes the resolved category back through
``caldav.update_time_log_entry``. Idempotent: re-running after a
clean migration is a no-op.

Use ``--dry-run`` to preview without writing.
"""

from __future__ import annotations

import argparse
import sys

from ..configs import DEFAULT_USER, resolve_config_path
from ..core.facade import Facade
from ..entities import TimeLogEntryUpdate
from .enums import TimeLogCategory
from .facade import CategoryFacade


def _format_change(uid: str, label: str, before: TimeLogCategory, after: TimeLogCategory) -> str:
    return f"{uid} ({label!r}): {before.value} -> {after.value}"


def run(dry_run: bool) -> int:
    """Backfill categories for every entry that lacks one.

    Only writes when inference has a confident signal (task
    alignment, history match, or keyword match). Entries with no
    signal stay as ``OTHER`` and are reported as "skipped".

    Returns 0 on success (including a no-op migration), 1 on a
    write failure.
    """
    config_path = resolve_config_path(DEFAULT_USER)
    facade = Facade(config_path)
    try:
        category_facade = CategoryFacade(facade.caldav)
        entries = facade.caldav.list_time_log(active_only=False)
        changed = 0
        skipped = 0
        for entry in entries:
            if entry.category != TimeLogCategory.OTHER:
                continue
            inferred = category_facade.infer(entry.label, entry.task_id)
            if inferred is None:
                skipped += 1
                continue
            print(_format_change(entry.uid, entry.label, entry.category, inferred))
            if not dry_run:
                result = facade.caldav.update_time_log_entry(
                    entry.uid,
                    TimeLogEntryUpdate(category=inferred),
                )
                if result is None:
                    print(f"  failed to update {entry.uid}", file=sys.stderr)
                    return 1
            changed += 1
        verb = "Would change" if dry_run else "Changed"
        print(f"{verb} {changed} entries; skipped {skipped} with no signal.")
        return 0
    finally:
        facade.shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m genesis.category.migration",
        description="Backfill missing time log categories via inference.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print changes without writing.",
    )
    args = parser.parse_args(argv)
    return run(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
