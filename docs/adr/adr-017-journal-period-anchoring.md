# 017. Journal period anchoring via `reference_date`

## Context

The previous codebase (`genesis-scaffolding`) enforced an invariant
that made periodic journals trivial to fetch and edit:

> Every week has at most one weekly journal entry. Every month has
> at most one monthly journal entry. Every year has at most one yearly
> journal entry.

The mechanism was a `(entry_type, reference_date)` pair on the SQL
`JournalEntry` row, with `reference_date` always normalised to the
period anchor:

- daily → the specific day
- weekly → Monday of that ISO week
- monthly → 1st of the month
- yearly → January 1

Tools and services treated `reference_date` as the canonical lookup
key. `create_journal` normalised the input, checked for an existing
row at the normalised date, and either inserted or refused with the
existing ID. Edit and read tools operated on the row ID alone; the
type and reference date were immutable.

The current codebase replaced this storage layer with markdown files
plus a `JournalFacade`. The replacement made a different choice: the
on-disk `date` field is "the calendar day the entry represents (for
daily) or the day it was created (for everything else)" (from the
facade docstring), and the only uniqueness check is on daily entries.
There is no weekly/monthly/yearly invariant at the storage layer.

Downstream code has been quietly compensating for the loss of the
invariant on the client side:

- `my-extensions/genesis/dates.py::normalize_journal_date` normalises
  client-side before sending.
- `my-extensions/genesis/cli.py` pre-checks `list_journals` for an
  existing entry at the normalised date and aborts with the existing
  UID if one is found.
- `my-extensions/genesis/queries.py::expected_slots` declares one
  weekly/monthly/yearly slot per period and `overview.find_missing_slots`
  reports gaps in the rendered report.

None of this is enforced server-side. Any client that does not run
those helpers — the Streamlit UI, a raw API call, a future
integration — silently creates a second weekly for the same week, or
files a weekly under an arbitrary weekday instead of Monday. The
"one entry per period" invariant is purely a convention the CLI
remembers to honour.

The PATCH endpoint compounds the problem: it accepts a new `date`
for any entry, including periodic ones, so a weekly can be moved
between weeks with a single HTTP request. Legacy scaffolding forbade
changing `entry_type` or `reference_date` post-creation.

## Decision

Split `Entry.date` into two fields with distinct responsibilities:

- `Entry.date` — caller-supplied at creation; for daily entries this
  is the calendar day, for periodic / misc / project entries this is
  the creation day. Immutable post-creation.
- `Entry.reference_date` — server-computed period anchor. daily →
  `date`; weekly → Monday of that ISO week; monthly → 1st of the
  month; yearly → January 1; project / misc → `None`. Immutable
  post-creation.

On disk the new field is stored as the YAML frontmatter key
`reference-date` (plain key, parallel to the existing `date`, `type`,
`title`).

Uniqueness for daily / weekly / monthly / yearly entries is enforced
on `(type, reference_date)`. project / misc continue to have no
uniqueness constraint — they are free-form notes, not periodic
anchors.

`PATCH /journals/{uid}` drops `date` from its accepted fields. The
update model never accepts `reference_date`. Both are creation-time
only; clients must delete and re-create to relocate an entry to a
different period.

The facade gains three helpers to support the new model:

- `find_for_period(type, on_or_in)` — generic lookup by type and any
  date inside the period; normalises the date server-side.
- `find_daily_for_date(date)` — kept as a thin wrapper that delegates
  to `find_for_period(DAILY, date)`. UI and tests keep their existing
  call sites.
- `repair_reference_dates()` — one-shot migration: walks the journal
  directory, computes the period anchor for any periodic entry that
  lacks `reference-date`, and re-saves. Returns a count of repaired
  entries and a list of (uid, error) pairs for entries that could not
  be repaired (e.g. collisions discovered after normalisation).

Legacy periodic entries without `reference-date` are invisible to
uniqueness checks until `repair_reference_dates()` runs against them.
This is the cost of backward-compatible read of old files; it is
bounded to entries written before this change and removed by a single
migration invocation.

The CLI's `normalize_journal_date` and `list_journals` pre-check
continue to work unchanged. With server-side uniqueness enforced the
pre-check becomes redundant for correctness, but the friendly error
message ("A weekly for 2026-01-12 already exists (ID: j-abc123)") is
worth the extra round-trip; keep it.

## Status

Proposed.

## Consequences

Positive:

- The legacy invariant is restored at the storage layer. Any client —
  CLI, UI, raw API, future integrations — that goes through
  `save_entry` is protected from creating duplicate periodic
  journals.
- The period anchor is computed server-side. Clients can pass any
  date and get the right anchor back in the response. There is no
  longer a way for a misbehaving client to file a weekly under the
  wrong day of the week.
- `find_for_period` gives the UI a single helper to surface "this
  week's weekly", "this month's monthly", "this year's yearly" —
  the parity the UI has had for daily entries since the journal page
  shipped.
- The PATCH surface shrinks. `date` and `reference_date` cannot be
  changed, so the only mutable fields on an entry are `title`,
  `content`, and project linkage. Easier to reason about; mirrors
  the legacy contract.
- Existing CLI code (normalisation, pre-check) keeps working without
  changes. The new server-side guard makes the CLI's behaviour
  authoritative rather than the only thing standing between the user
  and duplicate entries.

Negative:

- The wire model grows by one field (`reference_date`). Wire clients
  that ignored the old `date` field would also ignore the new one;
  wire clients that *did* rely on `date` semantics now have to learn
  the distinction. The new field is always present in responses so
  the contract change is visible.
- Legacy periodic entries that pre-date this change have
  `reference-date` absent on disk. They are not protected by the
  uniqueness check until `repair_reference_dates()` runs against
  them. The migration is opt-in, so a user who never runs it remains
  exposed to duplicates among pre-existing periodic entries only.
- The migration method does not auto-resolve collisions. If two
  legacy weeklies in the same week were never caught, the second one
  will fail to repair and the user has to delete one manually.
- A daily entry's `reference_date` equals `date` and is therefore
  redundant on the wire. Keeping it makes the contract uniform across
  types; removing it would complicate the periodic lookup helpers.

Neutral:

- The `Entry` model gains one optional field. The `_serialize` /
  `_parse` pair round-trips it the same way as `date` (ISO,
  unquoted, parsed by PyYAML).
- Frontmatter on disk gains one line per periodic entry. Daily
  entries also get it for uniformity. Legacy files without it parse
  fine; the field defaults to `None` on read, except for daily where
  it defaults to `date`.
