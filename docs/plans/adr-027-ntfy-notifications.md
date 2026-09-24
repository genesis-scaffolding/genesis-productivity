# Plan for adr-027

## Steps

1. **Confirm no new dep.** Stdlib `urllib.request` only. No
   `requests`, no `httpx` (httpx is dev-only; we don't promote
   it). Confirm with developer that stdlib is fine.

2. Add `DEFAULT_NTFY_URL = "http://p3:8092"` constant and
   `NtfyConfig(BaseModel)` to `src/genesis/configs.py`:
   - `url: str = Field(default=DEFAULT_NTFY_URL,
     description="ntfy server base URL.")`
   - `topic: str = Field(default="",
     description="Topic name. Resolved at Facade construction
     when empty.")`
   - `resolved_topic(user: str) -> str` method that returns the
     explicit topic when non-empty, otherwise builds
     `genesis-<safe_host>-<user>`.

4. Add `_safe_hostname() -> str` helper to
   `src/genesis/configs.py`:
   - `socket.gethostname()`, strip, lowercase, take first label
     (split on `.`), restrict to `[a-z0-9-]`, fall back to
     `"localhost"` on any error.
   - Module-level (not inside a class) so it is reusable from
     unit tests.

5. Wire `NtfyConfig` into `Configs`:
   - `ntfy: NtfyConfig = Field(default_factory=NtfyConfig)` on
     `Configs`.
   - Add `_build_ntfy(raw: dict, user: str) -> dict` that:
     - Reads `yaml_ntfy = raw.get("ntfy", {})` and
       `os.environ.get("GENESIS__NTFY__URL")` /
       `os.environ.get("GENESIS__NTFY__TOPIC")` overrides.
     - URL: env var → YAML → `DEFAULT_NTFY_URL`. Strip trailing
       `/` for consistency.
     - Topic: env var → YAML → empty string (resolved at
       Facade construction).
   - Call `_build_ntfy(yaml_data, user)` in `Configs.__init__`
     and assign into `merged["ntfy"]` before `super().__init__`.
   - Env-var override works automatically through `BaseSettings`
     (`GENESIS__NTFY__URL`, `GENESIS__NTFY__TOPIC`).

6. Create `src/genesis/notify/__init__.py` re-exporting
   `NotifyFacade`, the `messages` module, and the two builders.

7. Create `src/genesis/notify/facade.py`:
   - `class NotifyFacade` with `__init__(self, *, url: str,
     topic: str, timeout: float = 2.0)`.
   - `send(self, title: str, message: str, *, priority: int = 3,
     tags: list[str] | None = None) -> None`:
     - Build `endpoint = f"{self._url}/{self._topic}"`.
     - Use `urllib.request.Request(endpoint, data=message.encode("utf-8"),
       method="POST", headers={...})`. Headers include `Title`,
       `Priority`, and `Tags` (when tags non-empty).
     - `urllib.request.urlopen(req, timeout=self._timeout)`.
     - Read the response to drain the body, then close.
     - Do NOT swallow errors. Document the raises list.
   - No retry, no queueing.

8. Create `src/genesis/notify/messages.py`:
   - Pure functions returning `(title, message, tags)` tuples.
   - `session_started(session: TimeLogEntry) -> tuple[str, str,
     list[str]]`:
     - Title: `"Focus session started"`
     - Message: `"{label}"` (with task prefix when
       `session.task_id` is set: `"Task: <title>"`).
     - Tags: `["hourglass"]`
   - `session_milestone(session: TimeLogEntry, minutes_elapsed:
     int) -> tuple[str, str, list[str]]`:
     - Title: `"Focus milestone"`
     - Message: `"{label} · {minutes_elapsed} min"` (with task
       prefix when `session.task_id` is set: `"Task: <title> · {minutes_elapsed} min"`).
     - Tags: `["hourglass"]`
   - `session_finished(session: TimeLogEntry) -> tuple[str, str,
     list[str]]`:
     - Title: `"Focus session finished"`
     - Message: `"{label}"` (same task-prefix rule).
     - Tags: `["check"]`
   - Shared private `_label_for_session(session) -> str` for the
     task-prefix logic.

9. Wire `facade.notify` lazy property on
   `src/genesis/core/facade.py`:
   - Add `self._notify: NotifyFacade | None = None` to
     `__init__`.
   - Add `from ..notify.facade import NotifyFacade` to the
     `TYPE_CHECKING` block at the top.
   - `@property def notify(self) -> NotifyFacade:` lazy-cctor
     using `self.configs.ntfy.url` and
     `self.configs.ntfy.resolved_topic(self.configs.user)`.

10. Add `import logging` and a module-level `logger =
    logging.getLogger(__name__)` at the top of
    `src/genesis/ui/pages/focus.py` (file currently does not have
    one — confirm).

11. Wire notifications into
    `src/genesis/ui/pages/focus.py` and
    `src/genesis/ui/_render.py`:
    - Add a private `_notify_safely(builder: Callable[[], tuple[str,
      str, list[str]]]) -> None` helper at module scope of
      `focus.py`. Wraps the call in `try/except Exception`, logs
      a warning, never raises.
    - In `_render_active_sessions`, before iterating the
      sessions list, initialize a session-state set per session
      for already-fired milestones:
      `fired = st.session_state.setdefault(
        f"focus_notify_milestones_{s.uid}", set())`.
    - Compute `elapsed_minutes = int(elapsed.total_seconds() //
      60)`. The threshold is 30. Compute `milestone =
      elapsed_minutes // 30` (so 30→1, 60→2, 90→3, ...). Only
      fire when `milestone > 0` and `milestone not in fired`.
    - On a new milestone, append to `fired` and call
      `_notify_safely(lambda:
      messages.session_milestone(s, milestone * 30))`.
    - In the auto-stop countdown path, after the existing
      `facade.stop_session(...)` succeeds, call
      `_notify_safely(lambda: messages.session_finished(s))`.
      Manual Stop on a still-running countdown: no
      notification.
    - In `src/genesis/ui/_render.py`, import
      `from genesis.notify import messages` and the
      `_notify_safely` helper from `focus.py`. In
      `focus_create_dialog`'s `_foc_start` branch, after the
      existing `caldav.create_time_log_entry(...)` returns a
      non-None entry, fire
      `_notify_safely(lambda: messages.session_started(entry))`.
      Fire only on Start, not on dialog open or Cancel.
    - Wrap nothing else — the helper is the only catch.

12. Update `src/genesis/ui/pages/settings.py`:
    - Add a third expander `with st.expander("Notifications",
      expanded=False):` after the existing "Restore from
      backup" expander.
    - Render `facade.configs.ntfy.url` and
      `facade.configs.ntfy.resolved_topic(facade.configs.user)`.
      Read-only: caption explains YAML / env-var override.

13. Tests in `tests/test_notify_unit.py`:
    - `NtfyConfig`: defaults match the constants.
    - `NtfyConfig.resolved_topic`: empty topic returns
      `genesis-<safe_host>-<user>`; explicit topic returns the
      explicit value.
    - `_safe_hostname`: returns lowercase host, falls back on
      `socket.error`.
    - `NotifyFacade.send`: builds the right URL, the right
      method, the right body, the right headers. Verify by
      monkeypatching `urllib.request.urlopen` and inspecting
      the `Request` it received.
    - `NotifyFacade.send` propagates `URLError` and
      `TimeoutError` (does not swallow).
    - Message builders: deterministic output for a known
      `TimeLogEntry` (task-aligned and unaligned variants) for
      all three builders (`session_started`,
      `session_milestone`, `session_finished`).

14. Run `make format`, `make lint`, `make typecheck`,
    `make test`.

## File map

New files:

- `src/genesis/notify/__init__.py`
- `src/genesis/notify/facade.py`
- `src/genesis/notify/messages.py`
- `tests/test_notify_unit.py`
- `docs/adr/adr-027-ntfy-notifications.md` (this ADR)
- `docs/plans/adr-027-ntfy-notifications.md` (this plan)

Modified files:

- `src/genesis/configs.py` (`NtfyConfig`, `_safe_hostname`,
  `_build_ntfy`, `Configs.ntfy`)
- `src/genesis/core/facade.py` (`notify` lazy property)
- `src/genesis/ui/pages/focus.py` (milestone + finished
  notifications, `_notify_safely` helper)
- `src/genesis/ui/_render.py` (started notification in
  `focus_create_dialog`'s `_foc_start` branch)
- `src/genesis/ui/pages/settings.py` (Notifications expander)

No new dependencies.