# 034. Row-bound table widget

## Context

The shared task table (`render_tasks_table` in `src/genesis/ui/_table.py`)
edits tasks inline with `st.data_editor`. The editor identifies its rows
by *position*: the editing state it keeps is a mapping from row index to
the changed cells, and the table callback maps every row index back to a
task through the list of tasks that was rendered.

That mapping is only valid while the rendered row set and its order are
unchanged. The editor's editing state, on the other hand, lives in the
browser component, is keyed by row position, and survives as long as
that component survives. The browser sends the *whole accumulated* state
on every interaction, not only the cell the user just touched.

Four layers are involved:

1. the backend rows, which are authoritative;
2. the sort cache (ADR-023), which freezes the display order;
3. the widget generation, whose identity covers the schema, the
   disabled columns, and the row count, but not the row content;
4. the dispatch, which reads the editing state of the widget instance
   it was registered with.

Layers 2 and 3 disagree on what identifies a row. Layer 2 pins the
*order* of the rows. Layer 3 pins the *count* of the rows. Neither pins
the *identity* of the slots, so a row that leaves and a row that takes
its place look the same to the widget.

A recurrence series (ADR-033) makes this visible. Completing an
occurrence makes it terminal, so it leaves the filtered view, and the
series materialises a replacement occurrence at the tail of the window.
The table still shows the same number of rows, with the next occurrence
in the slot the user just edited. The browser keeps its accumulated
editing state, so the next interaction re-sends the stale edit, and the
callback dispatches it by position: the task that took over the slot
receives a status, a title, a priority, or a date the user never asked
for. The damage is one misrouted edit per stale slot, not a bulk
rewrite, and it is silent.

The ordinary case is safe by accident: completing an ordinary task
removes a row, the row count changes, the widget identity changes with
it, and the browser component is replaced. The series removes that
accident, which is why the failure was only ever seen with a series.

ADR-023 addressed the neighbouring problem. It freezes the display
order, so an inline edit does not move the rows and a positional
payload keeps pointing at the same task. It assumes the row set is
stable: it says nothing about a row being *replaced*. It also left the
Re-sort button as a live hole: a re-sort changes the order without
changing the count, and it does not invalidate the browser's editing
state, so a pending edit made before the re-sort was applied to the
task that took over that slot.

The mitigation that was believed to close this gap does not work.
`_handle_inline_edit` cleared the server's copy of the editing state
(`state["edited_rows"] = {}`) and the comment blamed Radicale's
unstable PROPFIND order. The browser owns the editing state: the next
interaction overwrites the server's copy with the browser's accumulated
payload, stale entry included. Clearing the server's copy protects
nothing.

The exposure is not limited to the `Status` column. Any inline column
can be misrouted the same way. The `Status` case is the loudest,
because it changes the visible work state and fires an XP event
(ADR-026) for a task the user never completed.

## Decision

We will bind the data editor widget instance to the row identity map.

The widget key of an editable table carries a short signature of the
ordered UIDs it was rendered with. The signature is the identity of a
rendered row map: it changes when a row is added, removed, or replaced,
and when the order changes.

```
rendered order: [O2, O3, O4, O5]  ->  signature s1  ->  key "global_tasks__s1"
rendered order: [O2, O3, O4, O6]  ->  signature s2  ->  key "global_tasks__s2"
```

A change of the row map therefore changes the user key, so Streamlit
registers a new widget instance with a new element id, so the browser
replaces the component, so the accumulated editing state is dropped. A
positional payload can only ever describe the map the user actually
interacted with.

The guarantee is server-side and checkable: the callback of a render
reads the editing state of the widget instance of that render, so a
payload addressed to a superseded instance is never dispatched. The
signature is computed over the *displayed* order, after the sort cache
has been applied.

The same treatment applies to the projects table and the journal table.
They dispatch by position as well, and a project or a journal entry
that is replaced while the count stays the same misroutes a pending
edit in the same way. The recurrence series table is read-only and
needs no change.

The rejected alternatives:

- **Detect the shift server-side.** The callback cannot tell an edit
  the user made in this generation from one carried over from an
  earlier generation. The only server-side record of the generation is
  the widget instance, which is what the fix binds.
- **A replay ledger.** Recording the dispatched (slot, task, column,
  value) tuples and dropping a repeat does not work: the stale edit is
  a different value on a different task in the same slot, so it is not a
  repeat, and the user legitimately editing the task that took over the
  slot would be silently dropped.
- **Put the UID in the dataframe index** and let Streamlit's own data
  signature carry the identity. It works, but it relies on an
  undocumented internal of the pinned Streamlit version, it changes the
  data sent to the browser, and it is invisible in the code.
- **Remount on every inline edit.** It churns the grid on every edit
  and does not cover the row-map changes that did not come from an
  inline edit (dialogs, the filter pills, a re-sort, an external
  write).

## Status

Proposed

## Consequences

Positive:

- An inline edit is always applied to the task whose row the user
  edited. No status, title, priority, or date can be written to a task
  the user never touched, and no XP event is fired for one.
- One mechanism covers every row-map change: an inline completion, a
  delete, a task or series dialog, a filter change, a re-sort, and a
  task created elsewhere.
- The Re-sort button is safe. It changes the order, hence the
  signature, hence the widget instance, so the pending edit is dropped
  with it. ADR-023 left that hole open.
- The row identity is visible in the widget key, so the rendered rows
  can be read off the key of the table.

Negative:

- The grid is re-rendered when the row map changes. The user sees the
  table reload (a short flicker) instead of the row updating in place.
  This is the price of correctness: the alternative is the silent
  corruption. The re-render happens only when the *row map* changes,
  not on every edit, and it is bounded the same way ADR-023 bounded the
  re-sort.
- The widget key changes with the row map, so the session state holds
  one entry per distinct row map until Streamlit's stale-widget cleanup
  removes the superseded one. The cleanup was measured to do so; if it
  ever does not, the fallback is an explicit pop of the superseded key.
- A late payload from a superseded widget is silently ignored. That is
  the intended behaviour, but it means an edit made in the same moment
  as a rerun that changes the row map is dropped. The user sees the
  cell revert. Losing an edit is much better than corrupting a task.

Neutral:

- No API, no backend, no schema change. The fix is in the widget.
- ADR-023 is not edited or superseded. It remains the decision about
  the frozen order; this ADR adds the decision about the slot
  identity. ADR-023's "Negative" section (users must click Re-sort to
  see a new ordering) still holds.
- The sort cache is unchanged. It keeps the UIDs of tasks that left the
  view, which is harmless for the ordering and does not affect the
  signature, which is computed over the rendered order.
- The contacts table still dispatches by position and keeps the same
  exposure. It is not covered by this decision.

## References

- ADR-023: cache-based stable sort. The frozen order, and the hole
  this ADR closes.
- ADR-033: recurrent task series. The renewal that keeps the row count
  stable while it replaces the row content.
- ADR-026: the game module. The XP event fired by a completion.
- streamlit/streamlit#17026: the upstream row-drift bug.
- `docs/plans/adr-034-row-bound-table-widget.md`: the implementation
  plan.
