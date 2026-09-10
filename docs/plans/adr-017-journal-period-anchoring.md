# Plan for adr-017

## Steps

1. **`src/genesis/journal/facade.py`**
   1. Add `reference_date: date_cls | None = None` to `Entry`.
   2. Add private helper `_normalize_reference_date(entry_type, date) -> date | None` that returns the period anchor for daily / weekly / monthly / yearly and `None` for project / misc.
   3. Extend `_serialize` to write a `reference-date` line in the frontmatter when the value is not `None`. Date values are unquoted ISO, same rule as `date`.
   4. Extend `get_entry` to parse `reference-date`. Default to `date` for daily entries (legacy files); default to `None` for everything else (legacy periodic entries are repaired by step 5).
   5. Rewrite `save_entry`:
      - Compute `normalized_ref = _normalize_reference_date(entry.type, entry.date)`.
      - If `entry.reference_date is not None`, validate it equals `normalized_ref` for daily / weekly / monthly / yearly. Raise `ValueError` on mismatch.
      - Compute `effective_ref = entry.reference_date or normalized_ref`.
      - For daily / weekly / monthly / yearly, look up `(type, effective_ref)` via the new `find_for_period` helper. Raise `ValueError` if a different UID owns the entry.
      - Persist `reference-date` when `effective_ref is not None`.
   6. Add `find_for_period(entry_type, on_or_in) -> Entry | None`. Generic helper. Daily / weekly / monthly / yearly only; returns `None` for project / misc.
   7. Reimplement `find_daily_for_date` as `return self.find_for_period(EntryType.DAILY, target_date)`.
   8. Add `repair_reference_dates() -> RepairReport` dataclass with `repaired: int` and `failures: list[tuple[str, str]]`. Walks the directory, re-saves any weekly / monthly / yearly entry whose `reference-date` is missing. Catches `ValueError` per-entry and records `(uid, error)` in `failures`. Never raises.

2. **`src/genesis/api/schemas.py`**
   1. Add `reference_date: datetime.date | None` to `JournalEntryOut`.
   2. Add `reference_date: datetime.date | None = None` to `JournalEntryCreateIn`.
   3. Remove `date` from `JournalEntryUpdateIn` (it is not added back).

3. **`src/genesis/api/routers/productivity.py`**
   1. In `create_journal`, pass `body.reference_date` into `Entry` so the caller can supply the anchor explicitly. Server still fills if omitted.
   2. In `update_journal`, drop the `body.date` branch. Use `existing.date` and `existing.reference_date` when constructing the new `Entry`.
   3. In `list_journals`, change the date-range filter from `e.date` to `e.reference_date`. Keep the `reference_date_start` / `reference_date_end` query param names; only the underlying field changes.

4. **`src/genesis/ui/pages/journal.py`**
   1. In `_render_list`, compute `existing_weekly`, `existing_monthly`, `existing_yearly` via `journal.find_for_period`. Add three "Open weekly / monthly / yearly" buttons next to the existing "Open daily" button. Each one sets `query_params["uid"]` to the entry UID and `query_params["edit"]="1"` before rerunning.
   2. The "+ New Entry" path is unchanged; the form already accepts weekly / monthly / yearly types. Server-side normalisation now ensures the resulting `reference_date` is correct.

5. **`tests/test_journal_unit.py`**
   1. Add a `TestReferenceDate` class:
      - daily stores `reference_date == date`
      - weekly with any weekday stores `reference_date == Monday`
      - monthly with any day stores `reference_date == 1st`
      - yearly with any day stores `reference_date == Jan 1`
      - explicit `reference_date` validated against normalised value (match ok, mismatch raises)
      - weekly uniqueness across the same ISO week (different weekday, same week, second save raises)
      - weekly uniqueness across different weeks (both succeed)
      - monthly / yearly uniqueness equivalents
      - `find_for_period(weekly, any_day_in_week)` returns the entry
      - `find_for_period(weekly, day_in_other_week)` returns `None`
      - `repair_reference_dates` repopulates legacy periodic files and returns `repaired == 1`
      - `repair_reference_dates` is idempotent (second run returns `repaired == 0`)
   2. Update existing `test_non_daily_types_have_no_uniqueness_constraint` to `test_periodic_types_have_uniqueness_within_period` and assert collisions raise.
   3. Keep `test_find_daily_for_date` working — the wrapper still finds daily entries.

6. **Documentation**
   1. Update the `JournalFacade` module docstring: drop the "calendar day ... or the day it was created" line. Document the two-field model: `date` is the creation day, `reference_date` is the period anchor.
   2. Update the `Entry` docstring to include `reference_date`.
   3. Reference `docs/adr/adr-017-journal-period-anchoring.md` in both.

7. **Verify**
   1. `make lint`
   2. `make typecheck`
   3. `make test` (unit tests only, fast)
   4. Spot-check a manual write + read of a journal file with `reference-date` in the frontmatter.
