# 029. Server-side focus scheduler on the Facade

## Context

The focus page (`src/genesis/ui/pages/focus.py`) renders active
sessions inside a `@st.fragment(run_every="1s")`. The fragment is
the only thing that drives focus session lifecycle notifications:

- **30-minute milestones.** Every tick, `_maybe_fire_milestone`
  walks active sessions, computes crossings since
  `last_milestone_minutes`, persists the new value, and fires
  one ntfy POST per crossing. ADR-027, ADR-028.
- **Countdown auto-stop.** Every tick, sessions with `now >=
  due` are stopped via `facade.stop_session`, then a
  `session_finished` ntfy notification is fired.

The fragment only reruns while a browser tab is alive. When the
user closes every browser (desktop, laptop, phone), the
fragment stops rerunning. Consequences:

- A 90-minute session running in the background never fires the
  30 / 60 / 90 minute notifications.
- A countdown whose `due` is reached in the background never
  auto-stops and never fires the "session finished"
  notification. The session sits as IN_PROGRESS in Radicale
  indefinitely.
- The user has no way to "come back later and see what
  happened" because nothing happened.

The source of truth for active sessions and fired milestones is
CalDAV (the `X-LAST-MILESTONE-MINUTES` X-property on each
VTODO). All the timing-driven events the fragment fires are
derivable from `now` plus the persisted session state. The
fragment is acting as a poorly-fitted scheduler.

The fix is to move timing-driven work to a process that does
not depend on browser connections. We have three long-lived
process candidates:

- **The Streamlit worker** (`make ui`). Survives all browser
  tabs being closed; workers recycle rarely (on code reload or
  restart).
- **The FastAPI server** (`make api` / `make serve`). Survives
  forever under uvicorn.
- **A standalone CLI** (`genesis scheduler start`). Useful for
  headless / systemd setups.

Forces:

- No new third-party deps. The scheduler is a `threading.Thread`
  plus a periodic tick. No `apscheduler`, no `celery`.
- The UI process must not have a peer dependency on the API
  process. Either of them should be able to host the scheduler
  independently. The user's mental model is "the Facade is the
  shared abstraction; UI and API are both clients of it."
- `last_milestone_minutes` and `stop_session` already provide
  the idempotency primitives needed for safe concurrent
  scheduling across processes (If-Match race protection,
  ADR-014, ADR-028). The scheduler should reuse them, not
  invent new state.
- The Streamlit fragment's 1-second UI tick stays. It drives
  live elapsed-time display, which is genuinely a UI concern.
  What moves out is the timing-driven *side effects*.
- Process lifecycle: the scheduler dies with the process that
  hosts it. This matches Radicale's behaviour today: `make
  serve` Ctrl+C kills the UI, the API, and Radicale. Adding
  detached-daemon machinery for the scheduler alone would be
  inconsistent.

## Decision

### New package: `genesis.focus_scheduler`

```
src/genesis/focus_scheduler/
  __init__.py         re-export FocusScheduler and runtime helpers
  scheduler.py        FocusScheduler: tick loop, start/stop
  runtime.py          per-process cache, ensure_scheduler_running, stop_all
```

Import direction: `genesis.focus_scheduler` depends on
`genesis.core` (for `Facade`), `genesis.caldav` (for `now_utc`),
`genesis.notify` (for `facade.notify.send`), and
`genesis.entities`. It does NOT import `genesis.ui`,
`genesis.api`, or `genesis.cli`. Same shape as `genesis.notify`
(ADR-027).

### `FocusScheduler`

```python
class FocusScheduler:
    """Tick loop that auto-stops expired countdowns and fires
    milestone notifications. Survives as long as the process
    that started it; dies with the process on shutdown.
    """

    def __init__(
        self,
        *,
        facade: Facade,
        tick_seconds: float = 5.0,
    ) -> None: ...

    def start(self) -> None:
        """Spawn the daemon thread. Idempotent."""

    def stop(self, *, timeout: float = 5.0) -> None:
        """Signal the thread to exit and join. Idempotent."""

    @property
    def is_running(self) -> bool: ...

    @property
    def tick_count(self) -> int:
        """Test seam: total ticks completed since start."""

    def _tick_once(self, now: datetime | None = None) -> None:
        """One iteration. Exposed for unit tests; production
        calls it from the daemon thread."""
```

The tick body is a near-verbatim lift of two pieces of code
that currently live in the focus page:

1. **Countdown auto-stop.** For each active session with
   `mode == COUNTDOWN`, `due is not None`, `now >= due`, and
   `status != COMPLETED`:
   ```python
   category = s.category  # already on the entry
   result = facade.stop_session(
       s.uid, actual_due=s.due, completed_at=now, category=category,
   )
   if result is not None:
       _notify_safely(
           lambda s=s: messages.session_finished(s, task_title=_task_title(s))
       )
   ```
   `stop_session` returns the updated entry on If-Match win
   and `None` on race loss (ADR-014). We fire the notification
   only when we win, so a cross-process race produces exactly
   one notification.

2. **Milestone notifications.** For each active session,
   compute `elapsed_minutes`, derive `crossings` since
   `last_milestone_minutes` (matching the logic in
   `_maybe_fire_milestone`), PUT
   `last_milestone_minutes = new_max`, fire one POST per
   crossing only when the PUT returns non-None (or refetch
   shows another device has already moved past `new_max`).
   Persist-before-notify ordering preserved verbatim (ADR-028).

Both call sites wrap their `facade.notify.send` in a private
`_notify_safely` (the same `try/except + logger.warning`
shape the focus page uses today). Errors that the scheduler
sees — CalDAV list failures, PUT failures, ntfy failures — are
all logged at WARNING and swallowed; the next tick retries.

The `_task_title` lookup uses `facade.caldav.get_task(s.task_id)`
which is cheap and the same call the focus page already makes.
A missing task (deleted race) returns `None` and the
notification body falls back to `session.label`.

The thread is a `threading.Thread(daemon=True)`. A `threading.Event`
named `_stop_event` is the cooperative-shutdown signal; the
tick loops until either the event is set or
`threading.Event.wait(tick_seconds)` returns False.

### Per-process runtime cache

The FastAPI app constructs a fresh `Facade` per request. We do
NOT want one scheduler thread per request. `runtime.py` keeps
a module-level dict keyed by username:

```python
# src/genesis/focus_scheduler/runtime.py
_schedulers: dict[str, tuple[FocusScheduler, threading.Thread]] = {}

def ensure_scheduler_running(user: str) -> FocusScheduler:
    """Return the scheduler for ``user``, starting one if needed.
    
    One scheduler thread per (process, user). Multiple Facades
    in the same process share this cache so the FastAPI path
    (per-request Facade) does not spawn a thread per request.
    """
    cached = _schedulers.get(user)
    if cached is not None:
        return cached[0]
    facade = Facade(resolve_config_path(user))
    scheduler = FocusScheduler(facade=facade)
    thread = threading.Thread(
        target=scheduler._run,
        name=f"focus-scheduler-{user}",
        daemon=True,
    )
    scheduler._thread = thread
    thread.start()
    _schedulers[user] = (scheduler, thread)
    return scheduler

def stop_for_user(user: str, *, timeout: float = 5.0) -> None:
    cached = _schedulers.pop(user, None)
    if cached is None:
        return
    scheduler, thread = cached
    scheduler.stop(timeout=timeout)

def stop_all(*, timeout: float = 5.0) -> None:
    for user in list(_schedulers):
        stop_for_user(user, timeout=timeout)
```

`atexit.register(stop_all)` is installed at module import so a
clean Python exit (return from `main`, normal `__main__`
shutdown) drains the threads. `signal.SIGTERM` / `SIGINT`
handlers in `genesis.ui._state._install_signal_handlers` are
extended to also call `stop_all`; today they shut down Radicale
on those signals and we add the scheduler to the same drain.

### Facade eager start, gated on `ensure_server`

```python
# src/genesis/core/facade.py
class Facade:
    def __init__(
        self,
        yaml_path: str | Path | None = None,
        *,
        ensure_server: bool = True,
    ) -> None:
        # ... existing init ...
        self._focus_scheduler: FocusScheduler | None = None
        if ensure_server:
            self._ensure_server_running()
            self._ensure_scheduler_running()

    def _ensure_scheduler_running(self) -> None:
        """Start the focus scheduler thread for this user.

        Lazy import keeps the dependency direction one-way:
        ``genesis.core`` does not import ``genesis.focus_scheduler``
        at module load. Tests that mock ``ServerManager`` also
        patch ``genesis.focus_scheduler.runtime.ensure_scheduler_running``
        to a no-op so the Facade stays cheap to construct.
        """
        from ..focus_scheduler.runtime import ensure_scheduler_running
        self._focus_scheduler = ensure_scheduler_running(self._configs.user)

    @property
    def focus_scheduler(self) -> FocusScheduler:
        """Return the focus scheduler for this user.

        Plain read; set eagerly in ``__init__`` when
        ``ensure_server=True``. Returns ``None`` when the
        Facade was constructed with ``ensure_server=False`` (a
        one-shot CLI read, the UI's nav peek, etc.).
        """
        return self._focus_scheduler
```

The scheduler is started in the same code path as Radicale.
The two share one precondition: "this caller is committing to
a long-lived process for this user." That is what
``ensure_server=True`` already means. The new opt-out is the
existing one; we do not add a second flag.

Why eager rather than lazy:

- The scheduler is a long-lived background side effect, not a
  handle to a stateless client (like ``caldav`` or ``notify``).
  Treating it as lazy would create a gotcha: a ``make ui``-only
  deployment never has anything that touches the property, so
  the scheduler never starts, and the bug we are fixing
  returns.
- Multiple Facades in one process (FastAPI per-request
  construction) all hit the same ``runtime._schedulers`` cache;
  the first one starts the thread, the rest no-op. The cost
  per construction is one dict lookup. Effectively free.
- The lifecycle is uniform: ``__init__`` starts Radicale and
  the scheduler together; ``shutdown`` stops them together;
  ``__enter__``/``__exit__`` work symmetrically.

`Facade.shutdown()` calls `stop_for_user(self._configs.user)`
before stopping Radicale. Order matters: stopping Radicale
first would make the next tick fail.

### Standalone CLI

```
genesis scheduler start --user <name>
```

Constructs a Facade for the user, starts the scheduler, blocks
on `scheduler._stop_event.wait()`. SIGINT / SIGTERM trigger
`stop_all` via the existing handler. Useful for systemd
deployments and headless setups where the user does not run
the UI or API at all.

```
genesis scheduler status --user <name>
```

Prints whether a scheduler is running in *this* process (the
runtime cache hit/miss). It does not detect schedulers in
*other* processes; cross-process liveness is out of scope for
this round.

### Focus page simplification

`_render_active_sessions` and `_maybe_fire_milestone` lose
their timing-driven logic. The page becomes a viewer:

- Remove `_maybe_fire_milestone` (logic moves into the
  scheduler).
- Remove the auto-stop block from `_render_active_sessions`.
  On the next tick after a scheduler-side auto-stop, the page
  re-lists active sessions, sees the session is COMPLETED, and
  drops it from the card list.
- Keep `@st.fragment(run_every="1s")` for live elapsed-time
  display.
- Keep `_resolve_category`, the category picker, Stop and
  Discard buttons — those are user-initiated, not
  time-driven.
- Remove `_notify_safely` from `focus.py` (only used by the
  removed logic).
- Update the module docstring to reflect the split.

### What this ADR does NOT do

- **No cross-process leader election.** Two processes can
  concurrently host schedulers for the same user. The cost is
  2x the CalDAV list traffic (still ~0.4 queries/s at a 5s
  tick across UI + API). The If-Match race protection keeps
  notifications deduplicated. Adding a PID-file lock is a
  future change if the duplicate work becomes noticeable.
- **No detached daemon.** The scheduler dies with its process,
  matching Radicale's behaviour. We do not introduce
  `start_new_session=True` subprocess spawning for the
  scheduler alone.
- **No notification preferences.** The user can still disable
  notifications by pointing `ntfy.url` at an unreachable host
  (ADR-027). This ADR does not change ntfy configuration.
- **No new config knob.** The 5s tick is hardcoded inside
  `FocusScheduler.__init__`'s default. A future ADR can lift
  it into `Configs` if it becomes something operators want to
  tune.
- **No status caption on the Focus page.** The earlier draft of
  this ADR proposed a small caption warning when the scheduler
  was not running. With eager start gated on
  ``ensure_server=True``, every Facade produced by production
  code paths comes with the scheduler running; there is no
  state where the caption would be true in practice. The page
  is purely a viewer.
- **No `last_milestone_minutes` migration.** Legacy VTODOs
  without the X-property already read back as 0 (ADR-028); on
  the first scheduler tick after deploy, they back-fire their
  elapsed crossings. This is the same behaviour as a page
  refresh on the old code.
- **No `session_started` notification from the scheduler.**
  The session-started fire happens in the focus-create dialog
  on the user's device (ADR-027) and is not a timing-driven
  event. The scheduler does not interfere with it.

### Tests

Add `tests/test_focus_scheduler_unit.py`:

- **Tick auto-stops an expired countdown and fires the
  finished notification.** Fake facade returns one
  IN_PROGRESS countdown with `due` in the past; tick runs;
  assert `facade.stop_session` was called and one
  `session_finished` POST went out.
- **Tick does not auto-stop sessions whose `due` is in the
  future.** Assert no `stop_session` call and no notification.
- **Tick auto-stop race loss does not fire notification.**
  Fake `stop_session` returns None (412); assert no POST.
- **Tick fires new milestone crossings and persists
  `last_milestone_minutes`.** Lift from
  `tests/test_focus_milestone_unit.py`.
- **Tick does not re-fire already-persisted milestones.**
- **PUT race loss skips notify (refetch shows moved).**
- **PUT transient failure skips notify, allows retry.**
- **Aligned session includes the task title in the
  notification body.**
- **Below-first-crossing tick is a no-op (no PUT, no POST).**
- **Scheduler can be started and stopped idempotently.**
  `start()` twice does not spawn two threads. `stop()` after
  never-started is a no-op. `stop()` twice is a no-op.
- **Daemon thread is `daemon=True`** so a hang in the tick
  loop cannot block process shutdown.
- **`runtime.ensure_scheduler_running` returns the same
  instance for repeated calls in the same process.**
- **`runtime.stop_for_user` removes the cache entry.**
- **Errors during a tick are swallowed.** Fake facade raises
  on `list_time_log`; the thread does not die; the next tick
  still runs.

Unit tests use a small fake facade with the four methods the
scheduler actually calls (`list_time_log`, `stop_session`,
`update_time_log_entry`, `get_time_log_entry`, `get_task`,
`notify.send`). The pattern mirrors
`tests/test_focus_milestone_unit.py`. The tick body is
exercised by calling `_tick_once(now=...)` directly so the
unit test does not depend on `time.sleep` and finishes in
well under a second.

No integration test this round. The scheduler's behaviour
against a real Radicale is the same as the focus page's
behaviour against real Radicale, which is already covered by
integration tests of the underlying CalDAV methods.

### Update documentation

- `README.md` — one short section under "Running" explaining
  that focus notifications require a long-lived process
  (`make serve`, `make api`, or `genesis scheduler start`)
  and that the Streamlit fragment is now a viewer.

## Status

Accepted.

## Implementation notes

Two corrections landed during implementation that this ADR
needs to record explicitly:

1. **Re-entrant lock.** The scheduler's internal
   :class:`Facade` is constructed with ``ensure_server=False``.
   Constructing it with ``ensure_server=True`` (the natural
   default) would call :meth:`Facade._ensure_scheduler_running`
   again, which would re-enter
   :func:`genesis.focus_scheduler.runtime.ensure_scheduler_running`
   while holding the runtime's :class:`threading.Lock`. The lock
   is not re-entrant; the recursion deadlocks. ``ensure_server=False``
   on the internal Facade is correct because the scheduler does
   not need to start a fresh Radicale (one is already running
   for the user, started by whichever :class:`Facade` came up
   first) and does not need to start a scheduler (it IS the
   scheduler).

2. **Startup delay before first tick.** ``FocusScheduler._run``
   waits ``tick_seconds`` BEFORE the first tick, not after.
   The reason is operational, not logical: Radicale is a
   single-threaded server. When the scheduler thread starts
   making CalDAV requests, it competes with any concurrent
   CalDAV probe from the :class:`Facade` that just started
   it. The probe (a TCP ``connect`` in
   :meth:`ServerManager.is_running`) blocks on Radicale's
   accept loop while the scheduler's first request is in
   flight. A freshly-started Radicale takes hundreds of
   milliseconds to index a new collection on first access,
   so the first CalDAV query (from the scheduler) and the
   concurrent TCP probe (from the just-started Facade) add
   the same latency to the API request that just constructed
   the Facade. Skipping the immediate first tick puts the
   scheduler's first CalDAV work at least ``tick_seconds``
   after the originating request completes, avoiding the
   contention. In production the cost is invisible (a 5-second
   delay before the first 30-minute milestone fires is well
   below the milestone's own precision).

## Consequences

Positive:

- The user's bug is fixed. A 90-minute session running in the
  background fires its 30/60/90 notifications; a countdown
  auto-stops and fires the finished notification; the user
  sees correct state when they reopen the page.
- No peer dependency between UI and API. Either can host the
  scheduler. Either or both can run.
- The 1-second UI tick is no longer load-bearing for
  notifications. It exists for live display.
- The scheduler is a thin, pure-stdlib layer. No new
  third-party deps.
- Idempotency primitives from ADR-014 / ADR-028 carry over.
  Cross-process races resolve to one notification per event.
- Process lifecycle mirrors Radicale. Same mental model:
  start `make serve`, both the Radicale server and the focus
  scheduler come up; Ctrl+C, both go down cleanly.

Neutral:

- One new package, three files. Two methods on `Facade`
  (`focus_scheduler` property, one `shutdown` extension).
  Two CLI subcommands (`start`, `status`).
- The focus page loses ~50 lines of timing logic. The
  scheduler gains it. Net code is roughly the same, but the
  scheduler is unit-testable without the Streamlit runtime.
- The daemon thread runs in every process that touches a
  Facade for a user. For `make serve` that is two threads; for
  FastAPI alone that is one thread per worker process. None
  of them do real work until the user starts a focus session
  — the per-tick cost is one CalDAV `list_time_log` query
  returning empty.

Negative:

- No cross-process leader election. Two processes running
  schedulers for the same user duplicate the CalDAV list and
  the PUT attempts. Acceptable at 5s tick (0.4 qps combined);
  not acceptable at 1s tick. If we ever drop the tick below
  ~2s we must add a PID-file lock.
- The scheduler dies with its process. If the user only ever
  runs `make ui` and never lets the Streamlit worker stay up
  (e.g. they restart their laptop), the scheduler is gone
  between sessions. This is no worse than Radicale today.
- The scheduler is started eagerly in ``Facade.__init__`` when
  ``ensure_server=True``. Tests that bypass the Radicale
  startup (the vast majority of unit tests, which pass
  ``ensure_server=False`` or use the ``MockCalDAVFacade``
  directly) do not touch the scheduler either. The handful of
  tests that construct a real ``Facade(configs_path,
  ensure_server=True)`` (currently just
  ``tests/test_facade_unit.py``) also patch
  ``genesis.focus_scheduler.runtime.ensure_scheduler_running``
  to a no-op so the Facade stays cheap to construct and does
  not leak threads across test cases.
- A bug in the scheduler tick crashes the daemon thread
  silently. We log a WARNING on every exception and the
  `daemon=True` flag ensures the process can still exit, but a
  bad tick leaves the scheduler running in a degraded state
  until the process restarts. We add a `tick_count` property
  and a `last_tick_at` timestamp on `FocusScheduler` so a
  future health check can surface stagnation.

## References

- ADR-004: module boundaries and facade pattern.
- ADR-014: focus timer VTODO model (auto-stop semantics, If-Match
  race protection).
- ADR-027: ntfy notifications for focus sessions.
- ADR-028: focus milestone persistence on the session
  (`X-LAST-MILESTONE-MINUTES`, persist-before-notify).
- ADR-026: game module — same package shape and lazy-property
  wiring this ADR mirrors.
- AGENTS.md: module boundaries, naming, testing model.
