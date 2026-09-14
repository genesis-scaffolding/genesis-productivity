# Plan for t6: focus session completion toast

## Context

Reported in `genesis-productivity-system-task-priorities.md`
(`t-ahxg1x`). Low-impact enhancement. The user wants a notification
to close the loop when a focus session ends.

The focus page already calls `st.toast(...)` for Start, Stop,
Auto-stopped, and Discard (`src/genesis/ui/pages/focus.py`). The
"Auto-stopped" toast is the close-the-loop case: a countdown session
that runs to completion while the user is on the Focus page. The
existing call uses the default toast duration, which is `"short"`
(about 4 seconds). For an auto-stopped session that the user might
not be actively watching, this can disappear before they notice.

The developer asked specifically for in-page toast only, not
cross-page. No new infrastructure.

## Steps

1. In `src/genesis/ui/pages/focus.py`, change the three stop-side
   toasts (Auto-stopped, Stop, Discard) to use `duration="long"` so
   they stay on screen for ~8 seconds instead of ~4. The Start toast
   stays at the default — that one confirms an action the user just
   took, so they are looking at the page.

   Keep the wording and emoji-free style consistent with the rest
   of the file.

2. Verify with `make lint`, `make typecheck`, `make test`. No new
   tests (UI-only behavior; existing tests must stay green).

3. Manual smoke test (document, do not gate): start a countdown
   session with a short duration, leave it running, confirm the
   auto-stop toast stays visible long enough to read and dismiss.

## Out of scope

- Cross-page toasts, browser notifications, system notifications.
  Developer explicitly rejected these.
- Sounds, banners, or other visible UI besides `st.toast`.
- Persisting the last completion notification across page loads.

## Files touched

- `src/genesis/ui/pages/focus.py`