# Plan for adr-034

## Steps

1. **`src/genesis/ui/_table.py`**
   - Add the module-level helper `_row_identity_signature(uids)`: a
     short, stable signature of a rendered row order.
   - In `render_tasks_table`, compute the widget key from the key
     prefix and the signature of the rendered rows, after the sort
     cache has been applied. Use it for the data editor and for the
     session-state read in the inline-edit callback.
   - Leave the row-action key and the Re-sort button key alone: a row
     action is resolved against the render it happened in.
   - Correct the comment at the end of the inline-edit callback and
     drop the dead "clear the pending edits" mitigation. The server's
     copy of the editing state is not what the browser sends back.
   - Update the docstrings of `render_tasks_table` and
     `render_filterable_tasks_table` with the new guarantee.
   - Apply the same helper to `render_projects_table` and
     `render_journal_table`. The recurrence series table is read-only
     and stays as it is.

2. **`tests/test_ui_table_unit.py`**
   - Add unit coverage for `_row_identity_signature`: determinism,
     order sensitivity, change detection, and a stable short length.

3. **`tests/test_ui_table_identity_unit.py`** (new)
   - Add the headless regression tests. They drive the real tasks page
     with `AppTest` and model the browser's editing state, so the
     payload the table receives is the one the browser would send.
   - The widget identity follows the row identity, and is stable when
     the rows do not change.
   - A payload of a superseded row map is never dispatched.
   - The next inline edit applies to its own row only.
   - An ordinary task completion still works.
   - A re-sort and a filter change drop the pending edit.
   - The projects table and the journal table bind their rows as well.

4. **Documentation**
   - Add ADR-034 and this plan. ADR-023 is not edited.

5. **Verify**
   - Run `make lint`.
   - Run `make format`.
   - Run `make typecheck`.
   - Run `make test`.
   - Run `make test-all` before the merge.

## Implementation notes

The implementation follows the steps above. The points below record
the decisions the implementation made where the plan left room.

- **The signature covers the displayed order.** It is computed after
  the sort cache has been applied, so it describes the rows the user
  actually sees, not the order before the sort.
- **The widget key is derived, not replaced.** The key stays
  `f"{key}__{signature}"`, so the key prefix still identifies the
  table and the session state still shows which row map it belongs to.
- **The dead mitigation is dropped in all three editable tables.** The
  line `state["edited_rows"] = {}` mutated the server's copy of the
  payload, which the browser overwrites on the next interaction. It
  protected nothing, and leaving it would keep the false model alive.
- **The callback reads the state of its own generation.** The callback
  closure captures the widget key of the render that registered it, so
  a payload addressed to a superseded instance is never dispatched.
- **The browser model in the tests resets on a new element id.** The
  editing state belongs to the component: a new element id means a new
  component, and the accumulated state is gone with it. The tests
  therefore send the payload the browser would send, and the server
  side guarantee they lock is the one that matters: a payload is only
  ever read from the widget instance of its own row map.
- **The projects and the journal tables are covered by the same
  helper.** Their exposure is lower, but the fix is the same three
  lines and the same helper, and leaving two tables with the known
  failure class would be a half fix.
- **The contacts table is left alone.** It dispatches by position as
  well, so it keeps the same exposure. It is out of the scope of this
  plan and is recorded as a residual risk in ADR-034.
