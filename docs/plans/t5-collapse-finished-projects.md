# Plan for t5: collapse finished projects in the projects list

## Context

Reported in `genesis-productivity-system-task-priorities.md` (`t-4z3hmm`).
Low-medium impact UX issue. The projects list renders every status
group at full size: In Progress, To Do, Completed, Canceled. As the
user accumulates finished work, those terminal sections take over the
page and bury the active projects.

The active sections (In Progress, To Do) should stay expanded so
they remain a quick reference. The terminal sections (Completed,
Canceled) should collapse into a labeled expander so the user can
still see the count and open the section on demand.

## Steps

1. In `src/genesis/ui/pages/projects.py`, modify `_render_list` so
   that sections for `ProjectStatus.COMPLETED` and
   `ProjectStatus.CANCELED` are rendered inside
   `st.expander(...)` with `expanded=False`. The label includes the
   count, matching the format used for the active sections (e.g.
   `Completed (3)`). The card grid rendering inside each terminal
   section is the same as the active section — extract a small
   helper `_render_project_card(project)` if the duplication gets
   noticeable, or keep the inline grid loop. Pick whichever keeps
   the diff smaller.

2. Order stays the same: In Progress, To Do, then Completed and
   Canceled (both collapsed).

3. Verify with `make lint`, `make typecheck`, `make test`. No new
   tests (UI-only behavior; existing tests must stay green).

4. Manual smoke test (document, do not gate): `make serve`, navigate
   to Projects with at least one completed project; confirm the
   "Completed" section is collapsed, label shows the count, and
   clicking the expander reveals the card grid.

## Out of scope

- Persisting expanded/collapsed state across sessions: `st.expander`
  resets on each script run. Acceptable for this branch.
- Reordering sections, hiding sections entirely, or adding a search
  filter: not requested.
- Filtering completed/canceled from the count shown on the
  Dashboard / Statistics page: out of scope.

## Files touched

- `src/genesis/ui/pages/projects.py`