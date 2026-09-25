# 028. Persist focus milestone notification state on the session

## Context

The focus page (`src/genesis/ui/pages/focus.py`) fires an ntfy
notification on every 30-minute crossing of an active session
(ADR-027). The "have we already fired for this crossing" flag
is stored in `st.session_state` keyed by session UID:

```python
fired_key = f"focus_notify_milestones_{s.uid}"
fired = st.session_state.setdefault(fired_key, set())
new_milestones = [m for m in range(1, milestone + 1) if m not in fired]
```

This works for a single Streamlit session that has not been
refreshed: the script reruns every second, but the set
persists across reruns, so each crossing fires exactly once.

It breaks on the two events that wipe `st.session_state`:

- **Page refresh.** The browser reloads the page; Streamlit
  rebuilds the session from scratch.
- **Phone sleep / wake.** The mobile browser suspends the tab;
  on resume, Streamlit rebinds the websocket to a fresh
  session.

In both cases the set is empty when the next render starts. If
the session is, say, 65 minutes old at that moment, the page
back-fires for both 30 and 60 in a single batch. The user sees
the same milestone notification they would have seen minutes
ago, again.

Multi-device use is a related but distinct concern: with the
current `st.session_state` approach, phone and laptop each
maintain their own set and each will fire the same milestone
notification on first render after refresh.

## Decision

Persist the milestone notification state on the
`TimeLogEntry` itself, as a new `last_milestone_minutes: int = 0`
field stored on the VTODO as `X-LAST-MILESTONE-MINUTES`. The
field holds the elapsed minute value of the highest milestone
crossing already notified for the session. Read it on every
active-session render; write it back through the existing
`update_time_log_entry` PUT path before firing the notification
POST.

### Field shape

```python
class TimeLogEntry(BaseModel):
    # ... existing fields ...
    last_milestone_minutes: int = 0
```

`TimeLogEntryCreate` and `TimeLogEntryUpdate` get the same
field. Default `0` means "never notified a milestone for this
session," which is correct for legacy VTODOs without the X-
property and for sessions that haven't yet crossed 30 minutes.

### iCal layer

`time_log_to_ical` writes `X-LAST-MILESTONE-MINUTES: <int>`
when `last_milestone_minutes > 0`; `time_log_from_ical` reads
it back, defaulting to `0` when the property is absent (legacy
VTODOs). `_populate` follows the same add-or-delete pattern as
the other X- fields.

### Logic in `_maybe_fire_milestone`

```python
def _maybe_fire_milestone(s, now: datetime) -> None:
    elapsed_minutes = int((now - s.start).total_seconds() // 60)
    last = s.last_milestone_minutes
    crossings = list(range(last + _MILESTONE_MINUTES,
                           elapsed_minutes + 1,
                           _MILESTONE_MINUTES))
    if not crossings:
        return

    # Persist BEFORE notifying. If the PUT fails (network, 412
    # race, 404), we skip the notify rather than fire twice.
    new_last = crossings[-1]
    result = facade.caldav.update_time_log_entry(
        s.uid,
        TimeLogEntryUpdate(last_milestone_minutes=new_last),
    )
    if result is None:
        refetched = caldav.get_time_log_entry(s.uid)
        if (refetched is not None
                and refetched.last_milestone_minutes >= new_last):
            return  # another device won the race; already fired
        return  # transient PUT failure; retry on next tick

    # Persist succeeded. Update the per-second in-memory cache
    # so we don't try to PUT every tick in the steady state.
    fired_max_key = f"focus_notify_max_{s.uid}"
    fired_max = st.session_state.get(fired_max_key, 0)
    st.session_state[fired_max_key] = max(fired_max, new_last)

    # Fire one POST per crossing.
    task_title = _task_title_for_session(s)
    for c in crossings:
        _notify_safely(
            lambda s=s, n=c: messages.session_milestone(s, n, task_title=task_title)
        )
```

The `st.session_state[fired_max_key]` cache is per-session, per-
page-load. It avoids a PUT every second in the steady state
(the elapsed minute counter ticks every second; only the
crossing seconds need a write). The cache is purely an
optimization — the durable field on the VTODO is the source of
truth.

### Ordering: persist before notify

We write the new `last_milestone_minutes` to CalDAV before
posting to ntfy. If the PUT fails for any reason, we skip the
notify. The tradeoff:

- **Persist-before (this ADR).** A PUT failure skips the
  notify. The user may miss a milestone on a CalDAV blip, but
  never gets a duplicate. A subsequent render catches up.
- **Persist-after (rejected).** A PUT failure means the
  notification already went out, but the next render will
  re-fire the same crossing. This recreates exactly the bug we
  are fixing, on a transient network failure, which is much
  more common than the Python-process-dies-between-two-HTTP-
  calls scenario that could cause a missed notification under
  persist-before.

The "miss on app crash between PUT and POST" failure mode is
acknowledged as a known small loss surface; it requires the
process to die inside a few-millisecond window after a
successful PUT, which is much rarer than a 1-second network
blip.

### Multi-device race handling

When phone and laptop both render the same session at the
same time, both compute the same `crossings` list from the
shared `last_milestone_minutes` value. Both attempt the PUT.
The CalDAV server's If-Match on the underlying object
(`update_time_log_entry` returns `None` on `ETagMismatchError`)
serialises them: whichever device's PUT lands first wins, the
other's PUT returns `None`, and that device refetches, sees
the field is already at the value it would have set, and
returns silently. Result: exactly one notification per
crossing per session, no matter how many devices are open.

### What this ADR does NOT do

- No migration CLI. Legacy VTODOs without
  `X-LAST-MILESTONE-MINUTES` read back as
  `last_milestone_minutes = 0`, which means "no milestones
  notified yet" — which is correct, because the previous code
  could not have notified them either (Streamlit state cannot
  persist across server restarts). Sessions that happen to be
  older than 30 minutes at the time of this ADR's deployment
  will back-fire all elapsed crossings on the next render
  after deploy, exactly as they would have under the old code
  on a fresh page load. This is the desired behavior: a deploy
  is a "soft refresh" of state.
- No change to the `_MILESTONE_MINUTES` constant or to the
  message builders.
- No change to the `session_started` or `session_finished`
  notifications. Those fire once per session lifecycle event,
  not on a recurring threshold, and do not need durable
  state.
- No change to the ntfy URL, topic, or facade. ADR-027 stands.

## Status

Proposed.

## Consequences

Positive:

- The bug is fixed. A page refresh, phone wake, or fresh
  page load does not re-fire already-notified milestones.
- Multi-device use is correct by construction. Phone and
  laptop cannot double-fire each other; whichever wins the
  CalDAV PUT race fires the notification, the other skips.
- The state lives where the session lives. There is no
  sidecar file to keep in sync, no orphan entries to clean up
  when a session is discarded.
- The in-memory `st.session_state` cache still avoids a PUT
  every second in the steady state, so the per-tick cost is
  one in-memory dict lookup per active session.

Neutral:

- One new field on `TimeLogEntry`,
  `TimeLogEntryCreate`, `TimeLogEntryUpdate`. One new iCal
  property `X-LAST-MILESTONE-MINUTES`. One new line in
  `_populate` and one new branch in `time_log_to_ical`.
- The `update_time_log_entry` PUT round-trip happens at most
  once per 30-minute crossing per session (not per second).
  CalDAV latency is dominated by the same Radicale round-trip
  the focus page already issues for stop / category updates.

Negative:

- The PUT happens on the same critical path as the notify
  POST. A slow Radicale response briefly delays the ntfy
  push. CalDAV PUT is typically under 100 ms on the same
  network; ntfy's POST is also under 100 ms. Total added
  latency to the first render that crosses a milestone: one
  extra PUT, ~100 ms. Acceptable.
- The 30-minute cadence is now reflected in the iCal wire
  shape via the unit of `X-LAST-MILESTONE-MINUTES`. If we
  later change `_MILESTONE_MINUTES`, the field name would be
  slightly misleading for new sessions. We rename the field
  alongside any future interval change.

## References

- ADR-004: module boundaries and facade pattern.
- ADR-014: focus timer VTODO model (the session lives here).
- ADR-027: ntfy notifications (this ADR extends that one).
- AGENTS.md: testing model (regression tests for the round
  trip).
