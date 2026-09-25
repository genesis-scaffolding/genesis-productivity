# 027. ntfy notifications for focus sessions

## Context

The focus page (`src/genesis/ui/pages/focus.py`) renders active
sessions with a one-second tick, auto-stops countdowns at the
target, and shows a Stop / Discard button per session. Today
nothing tells the user anything happened once they have moved
away from the tab: a 90-minute focus session ending in the
background is invisible, and a Pomodoro timer that auto-stops at
zero is easy to miss when the user is heads-down.

We want a lightweight notification path that surfaces two events
to the user without them watching the page:

1. Every 30 minutes of elapsed time on an active session, fire
   one notification.
2. When a countdown auto-stops (its `due` is reached), fire one
   notification.

The user already runs [ntfy](https://ntfy.sh) on their home
network (`http://p3:8092`). ntfy is a single HTTP POST per
message: the URL is the topic, the body is the message text, and
a handful of headers carry the title, priority, and tags. We do
not need a long-lived connection or a queue; one POST per event
is enough.

Forces:

- No new third-party deps allowed for this work. We use stdlib
  `urllib.request`.
- The focus page is the home of the timer logic. Putting
  notification firing there keeps the trigger logic close to the
  thing being measured.
- The notification module itself should not silently swallow
  failures. The module raises; the consumer (the focus page) is
  responsible for catching and ignoring so a dead ntfy server
  does not break the UI tick.
- `configs.yaml` is per-user. Anything that varies per user
  (e.g. the topic) is a per-user field. Anything that varies per
  deployment (the ntfy URL) is a per-user field too because the
  same product can target multiple homes.
- Settings page is already the home of account-level surfaces
  (download / restore backup). Adding a small "Notifications"
  expander there is the obvious place to surface the configured
  ntfy URL.

## Decision

### Module

New package `src/genesis/notify/`:

```
src/genesis/notify/
  __init__.py     re-export NotifyFacade and the message builders
  facade.py       NotifyFacade: one method, send(title, message, *, priority, tags)
  messages.py     pure functions that produce (title, message, tags)
                  tuples from a session + an event kind
```

Wired up on `Facade` as a lazy property `facade.notify`, parallel
to `facade.caldav`, `facade.journal`, `facade.profile`,
`facade.backup`, `facade.game`.

Import direction: `genesis.notify` depends on `genesis.core`
(for `Configs`) and `genesis.entities` (for `TimeLogEntry`). It
does not import `genesis.ui`, `genesis.api`, or `genesis.cli`.
This satisfies the AGENTS.md one-way-import rule.

### `NotifyFacade`

Single public method:

```python
class NotifyFacade:
    def __init__(self, *, url: str, topic: str, timeout: float = 2.0) -> None: ...

    def send(
        self,
        title: str,
        message: str,
        *,
        priority: int = 3,
        tags: list[str] | None = None,
    ) -> None:
        """POST to {url.rstrip('/')}/{topic}.

        Body: ``message`` (UTF-8).
        Headers:
          Title: {title}
          Priority: {priority}
          Tags: {','.join(tags)}  (when tags is non-empty)

        Raises:
            URLError, HTTPError, TimeoutError, OSError, ValueError.

        Does NOT swallow errors. The caller (focus page) wraps the
        call in try/except so a dead ntfy server cannot break the
        one-second tick.
        """
```

The constructor takes the resolved URL and topic from
`Configs.ntfy`. It does not look them up itself — `Configs` is
the single source of truth.

We do NOT add retries, queueing, or persistent state. ntfy is
best-effort: a missed notification on a transient outage is
acceptable. A dead server is fine; we want a fast failure (2 s
default timeout) so the focus page's tick is not blocked.

### Message builders

`messages.py` holds pure functions that turn a `TimeLogEntry`
plus an event kind into a `(title, message, tags)` tuple. They
do not touch the network. They live in `notify` (not `ui`) so a
future FastAPI consumer can reuse them.

Two helpers this round:

- `session_milestone(session, minutes_elapsed: int) ->
  tuple[str, str, list[str]]` — title `"Focus milestone"`,
  message `"{label} · {minutes_elapsed} min"`, tags
  `["hourglass"]`.
- `session_finished(session) -> tuple[str, str, list[str]]` —
  title `"Focus session finished"`, message `"{label}"`, tags
  `["check"]`.

Both helpers accept the `TimeLogEntry` directly and use its
`label`, `mode`, and `task_id` (when set, prefix with
`"Task aligned · "` so the user can disambiguate).

### `Configs.ntfy`

New pydantic model in `src/genesis/configs.py`:

```python
DEFAULT_NTFY_URL = "http://p3:8092"

class NtfyConfig(BaseModel):
    url: str = Field(default=DEFAULT_NTFY_URL, description="ntfy server base URL.")
    topic: str = Field(
        default="",  # resolved in model_post_init when empty
        description="Topic name. Defaults to genesis-{hostname}-{user}.",
    )

    @model_validator(mode="after")
    def _default_topic(self) -> "NtfyConfig":
        if self.topic:
            return self
        host = _safe_hostname()
        self.topic = f"genesis-{host}-{os.environ.get('GENESIS__USER', '')}".strip("-")
        return self
```

Actually that pattern (mutating after validation) is awkward and
violates pydantic conventions. Better:

```python
class NtfyConfig(BaseModel):
    url: str = Field(default=DEFAULT_NTFY_URL)
    topic: str = Field(default="")  # filled by Configs.__init__

    def resolved_topic(self, user: str) -> str:
        if self.topic:
            return self.topic
        return f"genesis-{_safe_hostname()}-{user}"
```

And `Configs.__init__` populates `ntfy.topic` via `_build_ntfy`
when the YAML did not set one. This matches the existing
`_build_dav` / `_build_paths` helpers (ADR-007).

`_safe_hostname()` is a tiny helper that returns the first label
of `socket.gethostname()` lowercased and stripped of anything
not `[a-z0-9-]`, falling back to `"localhost"` on any failure.
This avoids leaking FQDNs and any docker / kubernetes suffixes
into the topic name.

### Wiring on `Facade`

```python
# src/genesis/core/facade.py
@property
def notify(self) -> NotifyFacade:
    if self._notify is None:
        cfg = self.configs.ntfy
        self._notify = NotifyFacade(
            url=cfg.url,
            topic=cfg.resolved_topic(self.configs.user),
        )
    return self._notify
```

Same lazy-init pattern as the other sub-facades. The `Facade`
itself does not call `notify.send`; per the user's direction,
"the consumer (UI or FastAPI) decides when and what to
notify."

### Focus page integration

`src/genesis/ui/pages/focus.py` is the only caller this round.

Two integration points, both inside `_render_active_sessions`
(the `@st.fragment(run_every="1s")` fragment):

- **30-minute milestone.** For every active session, compute
  `elapsed_minutes = int(elapsed.total_seconds() // 60)`. The
  notification fires on the *crossing* of each multiple of 30:
  at elapsed = 30, 60, 90, ..., a notification fires. We track
  which multiples have already fired per session on the
  TimeLogEntry itself as
  `last_milestone_minutes` (`X-LAST-MILESTONE-MINUTES`); a
  per-page-load `st.session_state[f"focus_notify_max_{s.uid}"]`
  cache suppresses redundant CalDAV PUTs across the
  one-second tick. ADR-028 supersedes the original
  `st.session_state`-only dedup, which was wiped by page
  refresh and phone wake. We fire only the new milestones —
  if a session is 90 minutes old on first render, we fire for
  30, 60, and 90 in a single batch (one HTTP POST per
  milestone; the calls are sequenced, not parallel).
- **Countdown finished.** The existing auto-stop loop already
  fires when a countdown reaches its `due`. After the existing
  `facade.stop_session(...)` call succeeds, fire a finished
  notification. The notification only fires in the auto-stop
  path; manual Stop on a still-running countdown gets no
  notification (the user is at the page, they already know).

All three call sites wrap the notify call in
`try/except Exception` and log to `logger.warning` so a dead
ntfy server never breaks the fragment. The pattern is identical
to `Facade._fire_xp_safely` but lives in the consumer (page /
dialog), not on the facade, per the user's direction.

```python
def _notify_safely(builder: Callable[[], tuple[str, str, list[str]]]) -> None:
    try:
        title, message, tags = builder()
        facade.notify.send(title, message, tags=tags)
    except Exception as exc:
        logger.warning("ntfy notification failed: %s", exc)
```

### Settings page

Add one new expander to `src/genesis/ui/pages/settings.py`:
**Notifications**. Read-only this round (no edit surface):

- Render the resolved URL via `facade.configs.ntfy.url`
- Render the resolved topic via `facade.configs.ntfy.resolved_topic(facade.configs.user)`
- One-line caption: `Set ntfy.url and ntfy.topic in configs.yaml under the ntfy: key. Changes apply on next session.`

The expander sits below the existing "Restore from backup"
expander. No new sidebar entry — Settings is already the home of
account-level surfaces.

### What this ADR does NOT do

- No runtime mutation of the ntfy URL or topic. The user
  explicitly said this round will not provide any mechanism to
  modify the ntfy server URL at runtime. YAML / env-var only,
  picked up when the `Facade` is constructed.
- No multi-channel notifications (email, Pushover, Telegram).
  This ADR covers ntfy only. Adding a second channel is a future
  ADR if the user wants it.
- No per-event priority / tag overrides. Priority is fixed at 3
  (default); tags come from the message builder. ntfy priority
  and tag semantics are powerful but not part of this round.
- No notification preference / opt-out switch in the UI. ntfy is
  opt-in by configuration: if the URL is wrong, the POST fails,
  and the fragment catches the failure. The user can disable
  notifications by pointing the URL at an unreachable host
  (which makes every POST fail fast).
- No FastAPI / CLI surface. The module is reusable from those
  surfaces when we wire it; this ADR stops at UI.
- No tests against a real ntfy server. The integration suite is
  built around Radicale. ntfy mocking is done by patching
  `urllib.request.urlopen` in unit tests.

### Tests

Add `tests/test_notify_unit.py`:

- `NtfyConfig.resolved_topic` returns `genesis-<host>-<user>`
  when YAML does not set a topic. Host normalization strips FQDN
  suffixes. Falls back to `localhost` on hostname lookup
  failure.
- `NtfyConfig.resolved_topic` returns the YAML value when set.
- `NotifyFacade.send` issues `POST {url}/{topic}` with body =
  message, `Title`, `Priority`, `Tags` headers. Verified via
  `responses` (already a dev dep) against a mocked
  `urlopen`. Also patches `urllib.request.urlopen` directly so
  we exercise the real call path.
- `NotifyFacade.send` propagates `URLError` and `TimeoutError`
  (does not swallow them).
- Message builders produce deterministic
  `(title, message, tags)` for a known `TimeLogEntry`.
- Focus page wiring: not tested in this round. Streamlit
  fragments are hard to unit test without spinning up the
  Streamlit runtime; the integration suite does not exercise UI
  either. The wiring is small and reviewed by eye. The
  `messages.py` helpers are tested instead so the boundary is
  covered.

## Consequences

Positive:

- One stdlib POST per notification, no new dep. Easy to swap
  out if we ever want to switch transports.
- Per-user config keeps multi-user setups isolated: alice's
  topic differs from bob's by construction.
- Module raises, consumer catches. The choice is explicit and
  documented; future FastAPI consumers can decide their own
  failure policy.
- Notification firing logic stays close to the timer logic
  (focus page) where the decisions about "did we just cross a
  milestone" / "did the countdown just hit zero" already live.

Neutral:

- We add one new package `genesis.notify`. It crosses module
  boundaries (depends on `genesis.core` and `genesis.entities`),
  which is why this ADR is needed.
- We add a small settings expander; no new sidebar entry.
- Settings shows the resolved URL and topic read-only. A future
  ADR can add a Streamlit form-backed edit surface once we have
  a UI pattern for safe config writes.

Negative:

- The `Facade.notify` property eagerly resolves the topic. If
  the user later adds a runtime edit surface, the cached
  `NotifyFacade` will go stale until `clear_cached_facade` runs.
  Acceptable: the same is true for every other lazy facade on
  `Facade` and matches the existing pattern.
- No retry / queueing. A transient ntfy outage means a missed
  notification. We accept this in exchange for keeping the dep
  surface flat and the call path simple.
- Topic default depends on hostname. Two machines with the same
  username that share an ntfy server will collide on the default
  topic. Operators with that setup override `ntfy.topic` in
  YAML. This is a future-proofing note; today's user has one
  machine.
- The 30-minute threshold is hardcoded in the focus page. If we
  ever want it user-configurable, that is a future change
  (`NtfyConfig.milestone_minutes: int = 30`) and a small page
  edit.

## References

- ADR-004: module boundaries and facade pattern.
- ADR-007: pydantic settings refactor (the `_build_*` helper
  pattern this ADR follows).
- ADR-013: streamlit UI conventions (settings page is the home
  of account-level surfaces).
- ADR-026: game module — same package shape and lazy-property
  wiring this ADR mirrors.
- AGENTS.md: module boundaries, naming, testing model.