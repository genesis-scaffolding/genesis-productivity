"""One-time backfill: create XP events for completed tasks and sessions
that pre-date the game layer.

Source: the user's existing CalDAV collections (tasks and time-log).
Target: the new ``xp_events`` CalDAV collection. Each candidate
event is deduped by idempotency key against any pre-existing event,
so re-running is safe.

Usage
-----

Preview without writing::

    uv run python scripts/backfill_xp.py --user default --dry-run

Apply the backfill (writes to ``xp_events``)::

    uv run python scripts/backfill_xp.py --user default

Window by ``completed_at`` (UTC, inclusive lower / exclusive upper)::

    uv run python scripts/backfill_xp.py \\
        --user default --from 2025-01-01 --to 2026-01-01

Skip one side (e.g. only sessions, only tasks)::

    uv run python scripts/backfill_xp.py --user default --sessions-only
    uv run python scripts/backfill_xp.py --user default --tasks-only

Machine-readable summary::

    uv run python scripts/backfill_xp.py --user default --dry-run --json

Idempotency
-----------

Each candidate event uses the same idempotency key format as the live
facade: ``task:{uid}:completed`` for tasks and ``session:{uid}:stopped``
for sessions. The script fetches all existing ``xp_events`` once at
the start, builds a set of those keys, and skips any candidate whose
key is already present. Re-running is therefore safe; only the first
run writes events, subsequent runs are all-skipped.

If a live event was already created for a source UID (e.g. the user
re-completed a task after the game layer was deployed), the live
event is the one that persists — the backfill dedupes against it.

Fidelity notes
--------------

- The XP inference rules are duplicated from ``GameFacade._resolve``
  for performance: the script reads CalDAV directly instead of
  calling ``record_xp_event`` per source (which would be 2 HTTP
  round-trips per event). If the inference rules ever change, the
  script's copy of them must be updated in lock-step. See
  ``src/genesis/game/xp.py`` and ``src/genesis/game/classify.py``
  for the canonical implementation.
- Backfilled events carry ``reason="backfill: <title-or-label>"``
  so the events list shows the source the XP came from. Future
  agents reading the events log can distinguish backfilled events
  from live ones by inspecting the reason.
- Backfilled events do NOT rewrite historical ``created`` /
  ``modified`` on the source tasks or sessions — only
  ``occurred_at`` on the new XP event carries the historical
  timestamp.
- Game-layer state (``game.enabled``) is honoured: the script
  refuses to run when disabled, since writing events the user has
  explicitly turned off would surprise them on the next Character
  page load.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from genesis.configs import GameConfig, resolve_config_path
from genesis.core.facade import Facade
from genesis.entities import (
    Task,
    TimeLogEntry,
    XpEventCreate,
    XpEventKind,
)
from genesis.game.classify import infer_stat_from_text
from genesis.game.xp import (
    TASK_COMPLETION_XP,
    delta_from_session_duration,
    stat_from_session_category,
)

logger = logging.getLogger("backfill_xp")


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Bucket:
    """Per-stat aggregate for the summary output."""

    stat_id: str
    gained: int = 0
    lost: int = 0
    events: int = 0

    @property
    def net(self) -> int:
        return self.gained - self.lost


@dataclass
class Counters:
    """Mutable run counters, mutated as we walk the source data."""

    tasks_found: int = 0
    tasks_would_create: int = 0
    tasks_written: int = 0
    tasks_skipped_existing: int = 0
    tasks_failed: int = 0

    sessions_found: int = 0
    sessions_would_create: int = 0
    sessions_written: int = 0
    sessions_skipped_existing: int = 0
    sessions_failed: int = 0

    by_stat: dict[str, Bucket] = field(default_factory=dict)

    def bucket_for(self, stat_id: str) -> Bucket:
        b = self.by_stat.get(stat_id)
        if b is None:
            b = Bucket(stat_id=stat_id)
            self.by_stat[stat_id] = b
        return b


# ---------------------------------------------------------------------------
# Inference (mirrors GameFacade._resolve; see ADR-026)
# ---------------------------------------------------------------------------


def _resolve_task(task: Task, config: GameConfig) -> tuple[str, int, XpEventKind]:
    """Return (stat_id, delta, kind) for a completed task."""
    text = " ".join(filter(None, [task.title, task.description or ""]))
    stat_id = infer_stat_from_text(
        text, config=config, default_stat_id="discipline"
    )
    return stat_id, TASK_COMPLETION_XP, XpEventKind.GAIN


def _resolve_session(
    session: TimeLogEntry, config: GameConfig
) -> tuple[str, int, XpEventKind] | None:
    """Return (stat_id, delta, kind) for a completed session, or None
    to skip (no completed_at, no start, or classifier can't pin a stat
    for PERSONAL/OTHER with no keyword hits)."""
    from genesis.category.enums import TimeLogCategory

    if session.completed_at is None or session.start is None:
        return None
    text = session.label
    tier1 = stat_from_session_category(TimeLogCategory(session.category))
    if tier1 is not None:
        stat_id = tier1
    else:
        try:
            candidate = infer_stat_from_text(
                text, config=config, fail_on_no_match=True
            )
        except Exception as exc:
            logger.info(
                "skip session %s: classifier found no stat (%s)",
                session.uid, exc,
            )
            return None
        if candidate is None:
            return None
        stat_id = candidate

    duration_minutes = int(
        (session.completed_at - session.start).total_seconds() // 60
    )
    delta = delta_from_session_duration(duration_minutes, TimeLogCategory(session.category))
    kind = XpEventKind.GAIN if delta >= 0 else XpEventKind.LOSS
    return stat_id, delta, kind


# ---------------------------------------------------------------------------
# Walkers
# ---------------------------------------------------------------------------


def _in_window(when: datetime | None, from_dt: datetime | None, to_dt: datetime | None) -> bool:
    if when is None:
        return False
    if from_dt is not None and when < from_dt:
        return False
    if to_dt is not None and when >= to_dt:
        return False
    return True


def _parse_date_arg(raw: str | None) -> datetime | None:
    """Parse ``--from`` / ``--to`` (YYYY-MM-DD) into a UTC midnight datetime."""
    if raw is None:
        return None
    d = date.fromisoformat(raw)
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _walk_tasks(
    facade: Facade,
    from_dt: datetime | None,
    to_dt: datetime | None,
    existing_keys: set[str],
    config: GameConfig,
    counters: Counters,
    dry_run: bool,
) -> None:
    """Find completed tasks in window, build backfill candidates,
    persist (or skip) each."""
    all_tasks = facade.caldav.list_tasks()
    for task in all_tasks:
        if task.status.value != "completed":
            continue
        if not _in_window(task.completed_at, from_dt, to_dt):
            continue
        counters.tasks_found += 1
        key = f"task:{task.uid}:completed"
        if key in existing_keys:
            counters.tasks_skipped_existing += 1
            continue
        stat_id, delta, kind = _resolve_task(task, config)
        event = XpEventCreate(
            stat_id=stat_id,
            delta=delta,
            kind=kind,
            occurred_at=task.completed_at,
            idempotency_key=key,
            source_task_id=task.uid,
            reason=f"backfill: {task.title}",
        )
        if dry_run:
            counters.tasks_would_create += 1
            bucket = counters.bucket_for(stat_id)
            bucket.gained += delta
            bucket.events += 1
            continue
        try:
            facade.caldav.create_xp_event(event)
            counters.tasks_written += 1
            bucket = counters.bucket_for(stat_id)
            bucket.gained += delta
            bucket.events += 1
        except Exception as exc:
            counters.tasks_failed += 1
            logger.warning("failed to backfill task %s (%s): %s", task.uid, task.title, exc)


def _walk_sessions(
    facade: Facade,
    from_dt: datetime | None,
    to_dt: datetime | None,
    existing_keys: set[str],
    config: GameConfig,
    counters: Counters,
    dry_run: bool,
) -> list[str]:
    """Find completed sessions in window, build backfill candidates,
    persist (or skip) each. Returns labels of sessions the classifier
    could not classify (so the summary can echo them for the user)."""
    skipped_unclassifiable: list[str] = []
    all_sessions = facade.caldav.list_time_log()
    for session in all_sessions:
        if session.status.value != "completed":
            continue
        if not _in_window(session.completed_at, from_dt, to_dt):
            continue
        counters.sessions_found += 1
        key = f"session:{session.uid}:stopped"
        if key in existing_keys:
            counters.sessions_skipped_existing += 1
            continue
        resolved = _resolve_session(session, config)
        if resolved is None:
            counters.sessions_failed += 1
            if not session.label:
                skipped_unclassifiable.append(f"(uid={session.uid})")
            else:
                skipped_unclassifiable.append(session.label)
            continue
        stat_id, delta, kind = resolved
        event = XpEventCreate(
            stat_id=stat_id,
            delta=delta,
            kind=kind,
            occurred_at=session.completed_at,
            idempotency_key=key,
            source_session_id=session.uid,
            reason=f"backfill: {session.label}",
        )
        if dry_run:
            counters.sessions_would_create += 1
            bucket = counters.bucket_for(stat_id)
            if kind == XpEventKind.GAIN:
                bucket.gained += delta
            else:
                bucket.lost += abs(delta)
            bucket.events += 1
            continue
        try:
            facade.caldav.create_xp_event(event)
            counters.sessions_written += 1
            bucket = counters.bucket_for(stat_id)
            if kind == XpEventKind.GAIN:
                bucket.gained += delta
            else:
                bucket.lost += abs(delta)
            bucket.events += 1
        except Exception as exc:
            counters.sessions_failed += 1
            logger.warning(
                "failed to backfill session %s (%s): %s",
                session.uid, session.label, exc,
            )
    return skipped_unclassifiable


# ---------------------------------------------------------------------------
# Existing-key collection (one cheap read at start)
# ---------------------------------------------------------------------------


def _collect_existing_keys(facade: Facade) -> set[str]:
    keys: set[str] = set()
    for ev in facade.caldav.list_xp_events():
        if ev.idempotency_key:
            keys.add(ev.idempotency_key)
    return keys


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _bucket_row(b: Bucket) -> str:
    return (
        f"  {b.stat_id:<10} +{b.net:<6}  "
        f"(+{b.gained} gain, -{b.lost} loss, {b.events} events)"
    )


def _format_human(
    user: str,
    dry_run: bool,
    from_dt: datetime | None,
    to_dt: datetime | None,
    counters: Counters,
    skipped_labels: list[str],
    stat_order: list[str],
) -> str:
    verb = "Would create" if dry_run else "Created"
    lines: list[str] = []
    from_label = from_dt.date().isoformat() if from_dt else "(all-time)"
    to_label = to_dt.date().isoformat() if to_dt else "(all-time)"
    lines.append(
        f"Backfilling XP events for user '{user}' "
        f"from {from_label} to {to_label}..."
    )
    lines.append("")
    lines.append("Tasks:")
    lines.append(f"  Found {counters.tasks_found} completed tasks in window.")
    if dry_run:
        lines.append(f"  {verb}: {counters.tasks_would_create}")
    else:
        lines.append(f"  {verb}: {counters.tasks_written}")
    lines.append(f"  Already exist (skipped): {counters.tasks_skipped_existing}")
    if counters.tasks_failed:
        lines.append(f"  Failed: {counters.tasks_failed}")
    lines.append("")
    lines.append("Sessions:")
    lines.append(f"  Found {counters.sessions_found} completed sessions in window.")
    if dry_run:
        lines.append(f"  {verb}: {counters.sessions_would_create}")
    else:
        lines.append(f"  {verb}: {counters.sessions_written}")
    lines.append(f"  Already exist (skipped): {counters.sessions_skipped_existing}")
    if counters.sessions_failed:
        lines.append(f"  Failed: {counters.sessions_failed}")
    lines.append("")
    lines.append(f"By stat ({'would be ' if dry_run else ''}net XP):")
    for stat_id in stat_order:
        b = counters.by_stat.get(stat_id)
        if b is None:
            continue
        lines.append(_bucket_row(b))
    lines.append("")
    if dry_run:
        lines.append("Dry run — no events written. Re-run without --dry-run to apply.")
    if skipped_labels:
        lines.append("")
        lines.append(
            "Sessions the classifier couldn't pin a stat for "
            "(re-run with explicit stat_id via the API to recover):"
        )
        for label in skipped_labels:
            lines.append(f"  - {label}")
    return "\n".join(lines)


def _format_json(
    user: str,
    dry_run: bool,
    from_dt: datetime | None,
    to_dt: datetime | None,
    counters: Counters,
    skipped_labels: list[str],
    stat_order: list[str],
) -> str:
    payload: dict[str, Any] = {
        "user": user,
        "dry_run": dry_run,
        "from": from_dt.date().isoformat() if from_dt else None,
        "to": to_dt.date().isoformat() if to_dt else None,
        "tasks": {
            "found": counters.tasks_found,
            "would_create": counters.tasks_would_create if dry_run else counters.tasks_written,
            "skipped": counters.tasks_skipped_existing,
            "failed": counters.tasks_failed,
        },
        "sessions": {
            "found": counters.sessions_found,
            "would_create": counters.sessions_would_create if dry_run else counters.sessions_written,
            "skipped": counters.sessions_skipped_existing,
            "failed": counters.sessions_failed,
        },
        "by_stat": {},
        "skipped_session_labels": skipped_labels,
    }
    for stat_id in stat_order:
        b = counters.by_stat.get(stat_id)
        if b is None:
            continue
        payload["by_stat"][stat_id] = {
            "gained": b.gained,
            "lost": b.lost,
            "net": b.net,
            "events": b.events,
        }
    return json.dumps(payload, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "One-time backfill of XP events for completed tasks and sessions "
            "that pre-date the game layer."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  uv run python scripts/backfill_xp.py --user default --dry-run\n"
            "  uv run python scripts/backfill_xp.py --user default\n"
            "  uv run python scripts/backfill_xp.py --user default --from 2025-01-01\n"
            "  uv run python scripts/backfill_xp.py --user default --tasks-only\n"
        ),
    )
    parser.add_argument(
        "--user",
        default="default",
        help="Which user's data to backfill (default: 'default').",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Earliest completed_at to include (UTC, inclusive). Default: all-time.",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Latest completed_at to include (UTC, exclusive). Default: all-time.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the plan and print summary, write nothing.",
    )
    parser.add_argument(
        "--tasks-only",
        action="store_true",
        help="Skip sessions.",
    )
    parser.add_argument(
        "--sessions-only",
        action="store_true",
        help="Skip tasks.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Machine-readable summary as JSON.",
    )
    parser.add_argument(
        "--config-path",
        default=None,
        help="Explicit configs.yaml path. Default: auto-resolve from --user.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Per-row INFO logs (default: only warnings).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.tasks_only and args.sessions_only:
        print("ERROR: --tasks-only and --sessions-only are mutually exclusive", file=sys.stderr)
        return 2

    try:
        from_dt = _parse_date_arg(args.from_date)
        to_dt = _parse_date_arg(args.to_date)
    except ValueError as exc:
        print(f"ERROR: invalid date argument: {exc}", file=sys.stderr)
        return 2
    if from_dt is not None and to_dt is not None and from_dt >= to_dt:
        print("ERROR: --from must be earlier than --to", file=sys.stderr)
        return 2

    config_path = args.config_path or resolve_config_path(args.user)
    facade = Facade(config_path)
    try:
        if not facade.configs.game.enabled:
            print(
                "ERROR: game layer is disabled in configs.game.enabled. "
                "Set `game.enabled: true` and re-run.",
                file=sys.stderr,
            )
            return 3

        existing_keys = _collect_existing_keys(facade)
        counters = Counters()
        skipped_labels: list[str] = []

        if not args.sessions_only:
            _walk_tasks(
                facade, from_dt, to_dt, existing_keys,
                facade.configs.game, counters, args.dry_run,
            )
        if not args.tasks_only:
            skipped_labels = _walk_sessions(
                facade, from_dt, to_dt, existing_keys,
                facade.configs.game, counters, args.dry_run,
            )

        stat_order = [s.id for s in facade.configs.game.stats]
        if args.json_output:
            print(_format_json(
                args.user, args.dry_run, from_dt, to_dt,
                counters, skipped_labels, stat_order,
            ))
        else:
            print(_format_human(
                args.user, args.dry_run, from_dt, to_dt,
                counters, skipped_labels, stat_order,
            ))
        return 0
    finally:
        facade.shutdown()


if __name__ == "__main__":
    sys.exit(main())