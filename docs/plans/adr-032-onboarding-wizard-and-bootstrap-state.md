# Plan (round 2): onboarding wizard for Genesis Productivity

Rework of the round-1 plan after the owner's rejection (feedback
`j-451dvh`, decision of 3 Nov 2026). ADR updated in place at
`docs/adr/adr-032-onboarding-wizard-and-bootstrap-state.md`
(Status: Proposed, uncommitted).

---

## 0. Where this rework differs from round 1

Round 1 is the baseline. What changed, and why:

| Area | Round 1 | Round 2 | Driver |
|---|---|---|---|
| Step order | purpose, projects, tasks, journals, contacts (5) | **purpose, profile, life story, projects, tasks, journals, contacts (7)** | Gen 4 Nov: purpose moved to step 1 | feedback point 1 |
| Profile / life story | mentioned in the purpose copy, not collected | **two new steps, written through `facade.profile.write`** | feedback point 1 |
| Nav while pending | a `Setup` nav group first, the wizard is the first nav target; the user could navigate away | **no nav at all: `st.navigation({...}, position="hidden")` with the wizard as the only registered page; the sign-out control is suppressed too** | feedback point 2 |
| Dashboard banner | `st.info` + a "Start setup" button on the dashboard | **removed** (the dashboard is not reachable while pending; the banner would be unreachable code) | follows from point 2 |
| Nav once terminal | the page moves under `Account` as `Setup` | **no trace: the page is registered with `visibility="hidden"`, so it does not render in the nav bar** | feedback point 3 |
| Restart control | a "Run setup again" button on the wizard page itself | **a "Run setup again" button on the Settings page**; the wizard page is not in the nav, so it cannot host the control | feedback point 3 |
| Legacy rule | probe the account's data (projects / tasks / journal / contacts) through the live DAV server on the app's nav path; auto-skip if the account holds data; fail open (show the wizard) if the probe cannot run | **the state file's existence is the signal: the file is written at account creation; a missing file means the account predates the wizard and is treated as terminal** | point 2's interaction with the legacy rule (see 1.3) |
| State file written by | the wizard page, on first open | **`genesis.users.create_user`, at account creation** | the legacy rule above |
| `read_state` on a missing file | `not_started` | **`skipped` (terminal)**, i.e. "predates the wizard" | same |
| ADR | written at round 1 | rewritten to match | — |

What stands from round 1 and is unchanged in substance: the
`genesis.onboarding` module shape (unit-testable, no Streamlit), the
durable per-user JSON state, step persistence surviving `st.rerun()`,
every write going through the existing facade, and the substance of the
projects / tasks / journals / contacts steps. Starter projects stay
`Personal` + `Learning`. The out-of-scope list stands (no API endpoint,
no CLI, the state file is not in the backup archive).

---

## 1. Where I agree, and where I push back

### 1.1 Point 1 (profile and life story first) — agreed, implemented as two steps

Agreed, with the owner's 4 Nov adjustment: **purpose is step 1**, then the two profile / life-story steps, then the rest.
I chose **two steps, not one**: the repo already treats them as two
files with two pages (`pages/profile.py`, `pages/life_story.py`, two
`ProfileTarget`s in `genesis.profile`), and one step per file keeps the
"collect one thing, then the next" shape of the wizard.

I chose a **guided form into `facade.profile`** over reusing the
existing Profile page renderer (`genesis.ui._profile_page.render`).
The feedback explicitly allowed either. Reasons: the shared renderer
owns a title, an "Edit / Start writing" mode, a draft in session
state, and a History expander with a restore picker; that is a page,
not a wizard step. A wizard step wants one textarea, one Save, one
Next. The file format, the snapshot history, and every later edit are
unchanged, because the write still goes through `facade.profile.write`.

What the steps collect: freeform markdown, seeded with whatever is
already stored, with the prompts in the textarea placeholder (name,
timezone, what you do, what the assistant should know / what shaped
you). A structured form would have to be rendered back into markdown,
which buys nothing here.

### 1.2 Point 2 (no nav while pending) — agreed, with one deliberate extra

Agreed. The wizard owns the session until it is terminal. Three
mechanisms, all verified against the installed Streamlit 1.61.1:

- `st.navigation(pages, position="hidden")` hides the navigation widget
  (confirmed in `streamlit/commands/navigation.py`, the `position`
  parameter, and the frontend, which renders `NavigationProto.Position.HIDDEN`).
- Registering **only** the onboarding page while pending means no other
  page is routable. A URL for another page resolves to "page not found"
  and the wizard runs. This is stronger than hiding the nav, and it is
  what "the onboarding page is the only thing rendered in the session"
  asks for.
- `st.switch_page` raises `StreamlitAPIException` when the target page
  is not in the registry, so the pending path contains no
  `st.switch_page` call at all. (Round 1's dashboard banner used one.
  It is gone.)

**The one deliberate extra:** I also suppress the sign-out control in
the sidebar while the state is pending, because the feedback says the
onboarding page is the only thing rendered in the session, and the
sign-out button is the only other thing that would be on screen. The
wizard's "Skip setup" control is on every step, so the user is never
trapped. If the owner prefers to keep the sign-out button, it is one
line: move `render_logout_button()` back above the pending check in
`app.py`. Flagging it because it is a judgement call, not a requirement.

### 1.3 Point 2 x the legacy rule — agreed, and the legacy rule is changed

The feedback: "Gen's own existing account must not be locked into a
no-nav session, so the fail-open legacy auto-skip remains the guard".

Round 1's legacy rule was a **data probe** on the app's nav path: if the
account already held projects, tasks, journal entries, or contacts, mark
it `skipped`; if the probe could not run (the DAV server was down),
fail open and show the wizard. Under point 2, "fail open" no longer
means "the user gets the wizard and can navigate away". It means "the
user is locked into a page they cannot leave". That is the opposite of
what the feedback asks for, so round 1's fail direction is wrong for
this design.

**Round 2 replaces the probe with a fact.** The onboarding state file
is written when the account is created. A **missing state file means the
account predates this feature**, and a predating account is terminal:
the wizard never appears, the nav is never suppressed, the account is
never interrupted. The only way such an account gets the wizard is by
asking for it in Settings, which point 3 now provides.

Why this is the right call, stated explicitly:

- It cannot fail. The verdict is "does a file exist", not "does a
  server answer, and did the answer arrive". Gen's account has no state
  file, so it is terminal on its very first load after the upgrade and
  the nav is never suppressed for him.
- It costs nothing on the nav path. Round 1's probe needed a live DAV
  server during `app.py`, which meant either starting Radicale in the
  nav peek or querying a server that may not be up yet. Round 2's nav
  path reads one small local file.
- The cost is honest and small: an account created outside
  `create_user` (a hand-edited users.yaml, an old installation) is
  never offered the wizard automatically. Settings is the way in.

I am **not** disagreeing with the feedback: the auto-skip is still the
guard, and it still fails open toward the app. It is now structural
rather than heuristic. This is the one place where I changed a
round-1 decision that the feedback said "stands" (legacy accounts
failing open). The behaviour the feedback protects (Gen is not locked
in) is preserved; the mechanism changes. If the owner prefers to keep
the data probe, say so and I will add it back as an additional signal
on top of the file rule (it would then be a belt-and-braces check, and
the file rule would still be the primary one).

### 1.4 Point 3 (no trace in the nav, restart from settings) — agreed

Agreed. No Setup group, no Setup page under Account, nothing.

One implementation detail is worth stating because it is the difference
between "no trace in the nav bar" and "no trace at all": the onboarding
page stays **registered** with `st.Page(..., visibility="hidden")`.
The frontend filters hidden pages out before it builds the nav (verified
in the shipped `index.*.js`: the nav is built from `pages.filter(p => !p.isHidden)`),
so the nav bar shows nothing at all. Keeping the page registered buys
two things:

- the terminal summary can be shown at `/onboarding` without the app
  bouncing the user through a "Page not found" error toast and a
  redirect, and
- the Settings control can hand the session to the wizard with
  `st.switch_page("pages/onboarding.py")`, which raises if the page is
  not in the registry.

If the owner wants the page gone from the registry entirely, the price
is a "Page not found" toast on the finish transition and a manual
redirect to the dashboard. I recommend keeping it registered and hidden.

**What the reset clears**, named exactly, as the feedback asks:

- the status: `completed` / `skipped` -> `in_progress`
- the current step: back to the first step (`purpose`)
- the draft: cleared (the ticked starter-project names)
- `completed_at` / `skipped_at`: cleared
- `started_at`: set to now; `updated_at`: set to now; `attempts`: +1
- the state file is rewritten, not deleted, so the attempt counter and
  the timestamps survive

**What the reset does not touch:** the profile, the life story, the
projects (including the starter projects the wizard created the first
time), the tasks, the journal entries, the contacts. A restart is a
second tour of a populated account, not a reset of the account. The
project step does not duplicate the starter projects on the second run,
because it matches existing project names case-insensitively before
creating.

---

## 2. What I found (the new investigation for round 2)

**Profile and life story.**
`src/genesis/profile/` holds `ProfileFacade` with `write(target,
content, note=None)` (overwrite + auto-snapshot), `read`, `history`,
`restore`. `ProfileTarget.PROFILE` and `ProfileTarget.LIFE_STORY` map
to one markdown file each under `configs.paths.profile_root`
(`<data>/genesis/<user>/profile/`). `src/genesis/ui/pages/profile.py`
and `pages/life_story.py` are thin wrappers over the shared renderer
`src/genesis/ui/_profile_page.py`, which owns the title, the
view/edit modes, the session-state draft, and the History expander.
`facade.profile` already exists on the main facade
(`src/genesis/core/facade.py`, lazy property). The life story page's
own copy says it is "completely optional"; the wizard keeps that
property (the step warns when the file is still empty but never blocks
Next).

**How the nav can be suppressed.** `src/genesis/ui/app.py` rebuilds the
nav dict on every script run and calls `st.navigation(nav).run()`.
Streamlit 1.61.1 (the pinned version, `pyproject.toml`:
`streamlit>=1.61.1`) supports `position="hidden"` on `st.navigation`,
and `st.Page(..., visibility="hidden")` for a page that is routable but
not rendered in the nav. Both were verified in the installed package
(`streamlit/commands/navigation.py`, `streamlit/navigation/page.py`)
and in the shipped frontend bundle. `st.switch_page` raises
`StreamlitAPIException` for a page that is not in the current registry
(`streamlit/commands/execution_control.py`), so it is not usable while
the registry holds one page. `st.page_link` is the cheap alternative
for the terminal summary's link to the dashboard (no flush sleep;
`st.switch_page` sleeps 2 flush intervals, 0.01 s in this version, so
both are cheap here).

**Where the settings page lives.** `src/genesis/ui/pages/settings.py`
is a single flat script: a title, then three expanders ("Download
backup", "Restore from backup", "Notifications (ntfy)"). It uses
`facade.backup` and `facade.configs.ntfy` and keys its widgets
`settings_*`. A fourth expander, "Setup", fits the page's shape with
no restructuring. The reset button needs `st.switch_page` to hand the
session to the wizard, which is valid there because the onboarding page
is registered (hidden) in the terminal-state nav.

**Where accounts are created.** `genesis.users.create_user`
(`src/genesis/users/store.py`) is the single choke point: the signup
form (`src/genesis/ui/_auth.py::_signup_form`) and the CLI
(`src/genesis/cli/user.py::user_create`) both call it, and nothing else
creates accounts. The users table already keeps a `created_at` per user,
but it is not needed: the onboarding state file's existence is the
signal. `create_user` already imports `genesis.configs`, and
`genesis.onboarding` will be a leaf module (stdlib + pydantic), so the
new import edge `genesis.users -> genesis.onboarding` is leaf-ward and
creates no cycle.

**The state path.** `configs.paths.state` resolves to
`<XDG_STATE_HOME>/genesis/<user>/`, next to the users table at
`<XDG_STATE_HOME>/genesis/users.yaml`. That per-user directory is
unused today, so `onboarding.json` lands in a directory the app owns.

**The journal, task, project, and contact write paths** are unchanged
from round 1 and were re-checked: `task_create_dialog` ends with
`st.rerun()` (so the step must come from the state file),
`render_tasks_table` takes a `key` prefix, `JournalFacade.save_entry`
raises `ValueError` on a second entry for the same period and
`find_for_period` finds the owner, `ContactCreate(name=..., emails=[...])`
is the contact input shape.

---

## 3. The plan

### Goal

A new account goes from empty to populated through the wizard alone: a
profile and a life story written, starter projects created, one task
created through the existing add form, the journal's period anchoring
explained and demonstrated, the contact book introduced, and the
bootstrap marked done or skipped. While that is outstanding the wizard
owns the session: no nav, no way out but forward or Skip. Once it is
terminal the nav is exactly the nav it was before, and the wizard is
available again only by asking for it in Settings.

### Approach

1. A new domain module `genesis.onboarding` owns the durable bootstrap
   state. It holds no UI code and is unit-testable without Streamlit.
2. The state lives in one per-user JSON file at
   `<XDG_STATE_HOME>/genesis/<user>/onboarding.json`.
3. `app.py` decides the nav shape from that state on every script run:
   pending -> one hidden page, no nav; terminal -> the normal nav, with
   the onboarding page registered but hidden.
4. The current step is persisted in the state file, so a refresh or the
   `st.rerun()` at the end of the reused task dialog does not lose the
   user's place.
5. The state file is written when the account is created, so "no state
   file" is a reliable "this account predates the wizard" verdict.
6. Every write goes through the existing facade that owns that data.
   No wizard-only write path.

### Files and areas touched

#### New

| File | Contents |
|---|---|
| `src/genesis/onboarding/__init__.py` | Re-export `OnboardingFacade`, `OnboardingState`, `OnboardingStatus`, `OnboardingStep`, `STEP_ORDER`, `STATE_FILENAME`. |
| `src/genesis/onboarding/state.py` | `OnboardingStatus` (StrEnum), `OnboardingStep` (StrEnum), `STEP_ORDER`, `OnboardingState` (pydantic), `read_state(path)`, `write_state(path, state)` (atomic, mode 0600). |
| `src/genesis/onboarding/facade.py` | `OnboardingFacade`: the only entry point. |
| `src/genesis/ui/pages/onboarding.py` | The wizard page: the seven steps, the terminal summary. |
| `src/genesis/ui/_onboarding_copy.py` | The wizard prose, `STARTER_PROJECTS`, the profile / life-story prompts. Keeps the page file readable. |
| `tests/test_onboarding_unit.py` | Unit tests for the state machine (fast, no server, no Streamlit). |

#### Modified

| File | Change |
|---|---|
| `src/genesis/core/facade.py` | Add `self._onboarding: OnboardingFacade \| None = None` in `__init__`, a `TYPE_CHECKING` import, and a lazy `onboarding` property returning `OnboardingFacade(self._configs.paths.state / STATE_FILENAME)`. Same shape as `facade.profile`. |
| `src/genesis/ui/app.py` | Read the onboarding state in the existing `_username` block; build one of two navs; suppress the sign-out control while pending; register the onboarding page as `visibility="hidden"` in the terminal nav. |
| `src/genesis/ui/pages/settings.py` | A "Setup" expander: the current state, and a "Run setup again" button that calls `restart()` and then `st.switch_page("pages/onboarding.py")`. |
| `src/genesis/users/store.py` | `create_user` writes the initial onboarding state (`not_started`) for the new account, best effort. |
| `tests/test_users_unit.py` | Patch `XDG_STATE_HOME` / `XDG_CONFIG_HOME` in the `store` fixture so the new write lands in the tmp dir, not the developer's real home. Add a test that `create_user` produces a `not_started` state file. |
| `README.md` | The `Pages:` line loses the onboarding page (it is not a nav page) and gains one sentence about the first-run wizard and the Settings control. |

Not touched: `src/genesis/ui/pages/dashboard.py` (the round-1 banner is
removed rather than added), `src/genesis/ui/_render.py`,
`src/genesis/ui/_table.py`, `src/genesis/ui/_profile_page.py`,
`src/genesis/profile/*` (the wizard calls the facade, it does not
change it), the API, the CLI's other commands.

### Implementation steps

**1. `src/genesis/onboarding/state.py`**

```python
STATE_FILENAME = "onboarding.json"

class OnboardingStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"

    @property
    def is_terminal(self) -> bool:
        return self in (OnboardingStatus.COMPLETED, OnboardingStatus.SKIPPED)

class OnboardingStep(StrEnum):
    PROFILE = "profile"
    LIFE_STORY = "life_story"
    PURPOSE = "purpose"
    PROJECTS = "projects"
    TASKS = "tasks"
    JOURNALS = "journals"
    CONTACTS = "contacts"

STEP_ORDER: tuple[OnboardingStep, ...] = (
    OnboardingStep.PURPOSE,
    OnboardingStep.PROFILE, OnboardingStep.LIFE_STORY,
    OnboardingStep.PROJECTS, OnboardingStep.TASKS, OnboardingStep.JOURNALS,
    OnboardingStep.CONTACTS,
)

class OnboardingState(BaseModel):
    status: OnboardingStatus = OnboardingStatus.SKIPPED
    current_step: str | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    skipped_at: datetime | None = None
    attempts: int = 0
    draft: dict[str, str | list[str]] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    @property
    def never_run(self) -> bool:
        """True when the account was never offered the wizard."""
        return self.status is OnboardingStatus.SKIPPED and self.skipped_at is None
```

`read_state(path)`:
- the file is missing -> `OnboardingState(status=SKIPPED)`. A missing
  file means the account predates the wizard; the account is terminal
  and is never interrupted.
- the file is unreadable or invalid JSON -> log a warning and return
  `OnboardingState(status=SKIPPED)`. Same reason: a broken state file
  must never lock a user out, and treating it as terminal is the safe
  direction (the Settings control is the way back in).

`write_state(path, state)`: `path.parent.mkdir(parents=True,
exist_ok=True)`, then `tempfile.mkstemp` in the same directory, write
`state.model_dump_json(indent=2)`, `os.chmod(tmp, 0o600)`,
`os.replace(tmp, path)`, unlink the temp file on failure. Copy the
pattern from `src/genesis/users/store.py:98-114`.

**2. `src/genesis/onboarding/facade.py`**

`OnboardingFacade(state_path: Path)`:
- `state_path` property.
- `state() -> OnboardingState`: the file's contents, or the
  "predates the wizard" default.
- `is_pending() -> bool`: `not state().is_terminal`.
- `initialise() -> OnboardingState`: writes `not_started` with
  `started_at=now`. Used by the account-creation hook. Does not
  overwrite an existing file.
- `start(step=STEP_ORDER[0])`: `not_started` -> `in_progress`, sets
  `started_at` (first time only), `attempts += 1`, `current_step=step`.
- `set_step(step)`.
- `set_draft(key, values)`.
- `complete()`: `completed`, `completed_at=now`, `current_step=None`.
- `skip()`: `skipped`, `skipped_at=now`, `current_step=None`.
- `restart()`: `in_progress`, `current_step=STEP_ORDER[0]`, `draft={}`,
  `completed_at=None`, `skipped_at=None`, `started_at=now`,
  `attempts += 1`.
- Every mutation sets `updated_at=now(UTC)` and writes the file.
  Timestamps are UTC (`datetime.now(UTC)`).

**3. `src/genesis/onboarding/__init__.py`**: re-export the public names.

**4. `src/genesis/core/facade.py`**: wire `facade.onboarding` (lazy
property, `TYPE_CHECKING` import, one-way: `genesis.onboarding` must
not import `genesis.core`).

**5. `src/genesis/users/store.py`**: the account-creation hook.

```python
def _write_initial_onboarding_state(username: str) -> None:
    """Give a brand-new account its onboarding state file."""
    from ..configs import Configs, resolve_config_path
    from ..onboarding import STATE_FILENAME, OnboardingFacade

    configs = Configs(yaml_path=resolve_config_path(username))
    OnboardingFacade(configs.paths.state / STATE_FILENAME).initialise()
```

Called from `create_user` after the users-table write and the htpasswd
write succeed, inside a `try/except Exception` that logs a warning and
continues. A failure must not stop the account from being created: the
account then has no state file, is treated as terminal, and can still
be given the wizard from Settings.

**6. `src/genesis/ui/app.py`**: the nav gate.

```python
def _page(path, title, icon, url_path, *, visibility="visible") -> st.Page:
    return st.Page(str(path), title=title, icon=icon, url_path=url_path, visibility=visibility)

# ... after rehydrate_session() and the existing _username block:

_onboarding_page = _page(
    _PAGES / "onboarding.py", "Setup", ":material/rocket_launch:", "onboarding"
)

if _onboarding_pending:
    # The wizard owns the session: no nav widget, and the onboarding
    # page is the only page in the registry, so no other page can be
    # reached while the bootstrap is outstanding.
    st.navigation({"Setup": [_onboarding_page]}, position="hidden").run()
else:
    render_logout_button()
    nav = {
        "Productivity": [ ...unchanged... ],
        "User": _user_pages,
        "Account": [
            _page(_PAGES / "settings.py", "Settings", ":material/settings:", "settings"),
            # Registered but hidden: the nav bar shows nothing, the url
            # path keeps resolving, and the Settings control can hand the
            # session back to the wizard.
            _page(_PAGES / "onboarding.py", "Setup", ":material/rocket_launch:",
                  "onboarding", visibility="hidden"),
        ],
    }
    st.navigation(nav).run()
```

`_onboarding_pending` is read in the existing `_username` block, where
the app already builds a `Facade(resolve_config_path(_username),
ensure_server=False)`: `_onboarding_pending = _cfg.onboarding.is_pending()`.
That Facade is not shut down until the `finally`, as today. Reading one
small local file needs no DAV server, so the nav peek stays cheap.

`render_logout_button()` moves below the pending check so the sidebar
holds nothing while the wizard runs.

**7. `src/genesis/ui/_onboarding_copy.py`**: the prose.

`STARTER_PROJECTS = ("Personal", "Learning")`, `STEP_COPY:
dict[OnboardingStep, dict[str, str]]` with a title and a markdown body
per step, and `PROFILE_PROMPTS` / `LIFE_STORY_PROMPTS` for the
textarea placeholders.

Copy to use (wording is free, the substance is not):

- **profile**: the profile is what the assistant reads to know who it
  works for; it is one markdown file on your account; name, timezone,
  what you do, what your assistant should know about you; you can edit
  it any time; the assistant reads it too.
- **life_story**: the second file is the narrative history, what shaped
  you, the experiences, relationships, and turning points that explain
  where you are coming from; it is what lets the assistant respond with
  the right care when a conversation turns personal; optional, private
  to your account, editable or deletable at any time.
- **purpose**: three kinds of record (commitments through tasks and
  projects; life records and reflection through journaling, plus the
  Profile and Life Story pages; people through the contact book), and
  the point that all of it is stored so both the user and their personal
  assistant can read and write it, which is why tasks live in CalDAV,
  contacts in CardDAV, and journal entries are plain markdown files.
- **projects**: a project is an envelope for tasks, either because the
  tasks belong together or because the project is an outcome and the
  tasks are the concrete actions leading to it; a project can carry a
  start date and a deadline; `Personal` and `Learning` are preselected
  and the user can untick or add more.
- **tasks**: a task is one specific commitment and can carry up to three
  dates: scheduled start (turns the task into an appointment, appears on
  the Calendar page), hard deadline (firm delivery date, surfaced on the
  Dashboard when close), assigned date (the day you plan to act on it;
  unique to Genesis, it lets you plan when to act without inventing a
  fake deadline).
- **journals**: one daily entry per day, one weekly per week, one
  monthly per month, one yearly per year, and the system refuses a
  second entry for the same period; daily for morning planning, notes
  during the day, reflection at night; monthly and yearly for
  long-horizon plans; weekly as the bridge between the monthly plan and
  the daily plan.
- **contacts**: people and organisations, names, emails, phones,
  addresses, notes; stored in CardDAV so the assistant can read and add
  to it.

**8. `src/genesis/ui/pages/onboarding.py`**

```
facade = get_facade()
onboarding = facade.onboarding
state = onboarding.state()

if state.is_terminal:
    _render_terminal(state)      # the summary screen
else:
    _render_wizard(state)        # the seven steps
```

The terminal screen: the title "Setup", `st.success("Setup complete.")`
or `st.info("Setup skipped.")`, the completion timestamp, a one-line
recap of what the account now holds, a `st.page_link` to the dashboard,
and a line saying the wizard can be run again from Settings.

The wizard:

- If the status is `not_started`, call `onboarding.start()` first.
- The step comes from `state.current_step`, never from a local
  variable. `STEP_ORDER.index(step)` gives the progress
  (`st.progress((idx + 1) / len(STEP_ORDER), text=f"Step {idx + 1} of
  {len(STEP_ORDER)}")`).
- The step body, then the footer: Back (disabled on the first step),
  Next (labelled "Finish" on the last step), and a "Skip setup" control
  on the right that calls `onboarding.skip()` and reruns.

Step bodies:

- **profile** / **life_story** (one function, parameterised by target
  and key prefix):
  - the copy,
  - `facade.profile.read(target)` for the current content,
  - `st.text_area` seeded from the stored content through
    `st.session_state.setdefault(key, content)` (never pass `value=` to
    a widget that already has a session-state key),
  - a "Save" button -> `facade.profile.write(target, text,
    note="onboarding wizard")` -> a success line,
  - on Next (and on Back), if the text differs from what is stored, the
    same write happens before the step changes, so the user cannot lose
    what they typed by moving on,
  - if the file does not exist yet, a warning: "Your profile is still
    empty. Your assistant will have no context about you." Next is not
    blocked; the profile is optional, as the Profile page says.
- **purpose**: copy only.
- **projects**: the copy; a checkbox per starter project (pre-checked)
  plus a `st.form(..., clear_on_submit=True)` with a name input and an
  "Add" button that appends to the pending list and persists it in
  `draft["projects"]`; on Next, create every ticked or added project
  that does not already exist (case-insensitive name match against
  `facade.caldav.list_projects()`), then move to the tasks step.
- **tasks**: the copy; a "+ Add Task" button that calls the existing
  `task_create_dialog()` from `genesis.ui._render` (no new form), and
  the account's tasks rendered with the existing `render_tasks_table`
  from `genesis.ui._table`. The dialog's `st.rerun()` must not reset
  the step, which is why the step is read from the state file.
- **journals**: the copy; a small status grid showing, for DAILY /
  WEEKLY / MONTHLY / YEARLY, whether
  `facade.journal.find_for_period(entry_type, date.today())` already
  exists (this makes the anchoring visible); a text area plus a "Save
  today's daily entry" button. If a daily entry already exists for
  today, say so and point at the Journal page instead of creating a
  second one; otherwise
  `facade.journal.save_entry(Entry(uid=uuid4().hex, date=today,
  type=EntryType.DAILY, content=text))`, catching the `ValueError` the
  facade raises for a period clash.
- **contacts**: the copy; a name input plus an optional email input
  and an "Add contact" button calling
  `facade.carddav.create_contact(ContactCreate(name=..., emails=[email]
  if email else []))`; the contact count.

Widget keys, all prefixed `ob_`: `ob_profile_text`, `ob_profile_save`,
`ob_life_text`, `ob_life_save`, `ob_proj_{slug(name)}` for the project
checkboxes, `ob_proj_new` + `ob_proj_add` for the add-project form,
`ob_add_task`, `ob_journal_text` + `ob_journal_save`,
`ob_contact_name` + `ob_contact_email` + `ob_contact_add`, `ob_back` +
`ob_next` + `ob_skip`. No overlap with the `_ntd_*` keys inside
`task_create_dialog`.

**9. `src/genesis/ui/pages/settings.py`**: the reset control.

A fourth expander, "Setup", after the notifications expander:

- the current state, read through `facade.onboarding.state()`:
  "Not started", "In progress (step N of 7)", "Completed on <date>",
  "Skipped on <date>", or "Not run for this account" (the
  `never_run` case: the account predates the wizard).
- a "Run setup again" button (disabled while the state is pending,
  which cannot happen from this page because the page is unreachable
  while pending, but the guard is cheap):
  ```python
  if st.button("Run setup again", key="settings_onboarding_restart"):
      facade.onboarding.restart()
      st.switch_page("pages/onboarding.py")
  ```
- a line under the button: "This starts the setup wizard again. It does
  not change your data: your profile, projects, tasks, journal entries
  and contacts stay as they are."

**10. `tests/test_onboarding_unit.py`** (fast, no server, no
Streamlit):

- a missing file reads as terminal (`skipped`) and `is_pending()` is
  False;
- a corrupt file reads as terminal and logs a warning;
- a round trip write/read;
- `initialise()` writes `not_started` and `is_pending()` is True;
- `initialise()` does not overwrite an existing file;
- `complete()` and `skip()` are both terminal and `is_pending()` is
  False for both;
- `restart()` from a terminal state returns to `in_progress` at the
  first step, bumps `attempts`, clears the draft, and clears
  `completed_at` / `skipped_at`;
- `start()` from `not_started` sets `in_progress` and the first step;
- `set_step()` / `set_draft()` round trip;
- a failed write leaves no temp file behind;
- two state paths do not share state;
- `STEP_ORDER` has the seven steps in the agreed order and
  `STEP_ORDER[0] is OnboardingStep.PURPOSE` (purpose first, per the
  owner's 4 Nov adjustment).

**11. `tests/test_users_unit.py`**: patch `XDG_STATE_HOME` and
`XDG_CONFIG_HOME` to the tmp dir in the `store` fixture (so the new
write in `create_user` does not touch the developer's real home), and
add a test that `create_user` leaves a `not_started` onboarding state
for the new account and that `delete_user` does not remove it (the
state is not the users table).

**12. `README.md`**: the `Pages:` line and one sentence about the
first-run wizard and the Settings control.

**13. Run the QA plan below.**

### QA plan

In-repo checks, all from the worktree root:

- `make lint` (ruff check --fix over `src/` and `tests/`)
- `make format` (ruff format) if the implementer prefers explicit
  formatting
- `make typecheck` (pyright over `src/` and `tests/`)
- `make test` (unit suite, fast)
- `make test-all` (adds the integration suite, about a minute)

Manual smoke test (`make ui`, then in the browser):

1. **A brand-new account.** Sign up. The app opens on the wizard, step
   1 of 7. The sidebar shows **no nav at all** and no sign-out button;
   the wizard is the only thing on screen.
2. **Purpose step.** The copy renders. Press Next.
3. **Profile step.** The copy renders, the textarea is empty, the
   warning about the empty profile shows. Type a profile, press Save:
   "Saved." appears. Press Next: the step moves to the life story and
   the file is written under
   `<data>/genesis/<user>/profile/profile.md`.
4. **Life story step.** Same shape. Leave it empty and press Next: the
   warning shows, Next still works.
5. **Projects step.** `Personal` and `Learning` are ticked. Untick one,
   add "Test project". Press Next. Both ticked projects exist on the
   Projects page afterwards, and no duplicates.
6. **Tasks step.** Click "+ Add Task", fill the existing form, Add.
   The task lands in the table and the wizard stays on the Tasks step
   (the dialog's `st.rerun()` must not reset the step).
7. **Journals step.** Write today's daily entry, save. It appears on the
   Journal page. Reload the browser: the wizard resumes on the same
   step. Try to save a second daily entry: it is refused with a
   readable message.
8. **Contacts step.** Add a contact, press Finish.
9. **Terminal.** The normal nav is back (Productivity / User /
   Account with Settings). There is no Setup entry anywhere in the nav
   bar. The terminal summary is shown with a link to the dashboard.
10. **No trace.** Reload, navigate to every page: the wizard never
    appears, the nav never shows it.
11. **Restart from Settings.** Settings -> Setup -> "Run setup again".
    The wizard takes over the session, the nav disappears again, and it
    starts at step 1. The existing profile, projects, task, journal
    entry, and contact are all still there. Press Finish: the nav comes
    back.
12. **No duplicate starter projects.** After the restart, the projects
    step shows `Personal` and `Learning` ticked; press Next without
    ticking anything new: no duplicate project is created.
13. **Legacy account.** Sign in as an account that predates the wizard
    (no state file): the nav is the normal nav, no wizard, no banner,
    no lock. Open Settings -> Setup: it says "Not run for this
    account", and "Run setup again" gives the wizard.
14. **Escape attempts while pending.** While the wizard is running:
    type `/dashboard` in the URL bar: the wizard is still the only
    page that renders (the browser shows a "page not found" message
    first). The browser Back button does not reach another page.
15. **Two accounts.** Sign out, sign in as a second, brand-new account:
    that account gets its own wizard; the first account's state is
    untouched.

### Risks

- **`st.rerun()` from the reused task dialog.** The wizard's step must
  come from the durable state, not a local variable. Handled by design;
  covered by smoke step 6.
- **Widget key collisions** between the wizard and the reused dialogs.
  All wizard keys are `ob_`-prefixed; `task_create_dialog` uses
  `_ntd_*`; the profile page uses `profile_*` and `life_story_*` (the
  wizard does not use that renderer, so there is no overlap).
- **`st.session_state` mutation after widget creation.** Streamlit
  raises when a widget key is set after its widget is instantiated.
  Seed with `setdefault` before creating the widget; use a
  `st.form(clear_on_submit=True)` for the add-project input rather than
  clearing the key after the fact.
- **The onboarding page stays routable after the wizard is done.** It
  is hidden from the nav but answers at `/onboarding`. This is
  deliberate (it is what makes the terminal summary and the Settings
  hand-off work without a "page not found" toast). If the owner wants
  it gone from the registry, the finish transition needs a manual
  redirect and the Settings button needs a URL-based hand-off instead
  of `st.switch_page`.
- **A manually typed URL while the wizard is running** produces a
  "page not found" message from the browser before the wizard renders
  again. The user cannot reach another page. Acceptable, but it is
  visible.
- **The sign-out control is hidden while the wizard runs.** A user who
  wants to sign out has to finish or skip first. One-line revert in
  `app.py` if the owner disagrees.
- **The account-creation hook fails** (a permissions error on the state
  dir). The account is created, has no state file, is treated as
  terminal, and never sees the wizard until it is asked for in
  Settings. The failure is logged. This is the safe direction.
- **`create_user` now writes outside the users table.** The existing
  unit tests for the users table call it, so the test fixture has to
  patch `XDG_STATE_HOME` / `XDG_CONFIG_HOME` or the developer's real
  home is polluted. Noted in the implementation steps.
- **An account created outside `create_user`** (hand-edited
  users.yaml, an old installation) is never offered the wizard
  automatically. Settings is the way in.
- **Radicale unreachable during the wizard.** The wizard's writes are
  the same writes the real pages make; a failure surfaces as an error on
  the step. The state file is unaffected, so the wizard resumes.
- **Duplicate projects** if the wizard runs twice. Mitigated by the
  case-insensitive name check before creating.
- **Duplicate daily journal entries** are refused by
  `JournalFacade.save_entry`; the page checks `find_for_period` first
  and catches the `ValueError`.
- **The state file is not in the backup archive** (ADR-024). Restoring
  onto an account leaves the wizard pending. Deliberate, recorded as
  out of scope in the ADR.
- **No Streamlit test harness** exists, so the UI is verified by the
  manual smoke test. This matches the precedent in
  `docs/plans/dialog-direct-invocation.md`. All logic that can be
  tested outside Streamlit lives in `genesis.onboarding` and is covered
  by `tests/test_onboarding_unit.py`.

### Open questions for the coordinator

1. **The legacy rule changed shape** (data probe -> the state file's
   existence). This is the one place where I changed a round-1 decision
   the feedback said "stands". The protected behaviour (Gen is not
   locked into a no-nav session) is preserved and is now
   failure-proof. If the owner wants the data probe kept as well, say so
   and I will add it back as a secondary signal.
2. **The sign-out control while the wizard runs.** I suppress it. One
   line to keep it.
3. **The onboarding page stays routable but hidden after the wizard is
   done.** I recommend it; the alternative costs a redirect.
4. **Starter projects** stay `Personal` + `Learning` (unchanged from
   round 1).
5. **Out of scope** stays: no API endpoint, no CLI, the state file is
   not in the backup archive.

---

## 4. What I could not finish

- No code was written beyond the ADR, as instructed. Nothing is
  committed; `git status` shows only the untracked ADR file.
- The repo convention puts plans in `docs/plans/adr-NNN-<title>.md`.
  This plan is delivered here and at `/tmp/se-t-bqekub-plan-r2.md`,
  rather than written to that path, to avoid two agents writing the
  same file. Say the word and I will save it as
  `docs/plans/adr-032-onboarding-wizard-and-bootstrap-state.md`.
- The upstream toolkit reference (t-8e3g38) is design only; there is no
  implemented wizard to read, so the mapping from its ADR to this plan
  is my reading, not a port.
- I could not verify the browser behaviour of an **empty** sidebar (no
  nav widget, no sign-out button). Streamlit hides a sidebar with no
  elements in the versions I know, but I did not confirm it in the
  shipped bundle. Smoke step 1 checks it, and the fallback is that a
  thin empty sidebar frame remains, which is cosmetic.

---

## 5. Implementation record

Implemented on branch `se/t-bqekub-onboarding-wizard` against the
plan above. The plan is unchanged; this section records what the
code actually does where the plan left a detail open.

- **Files.** New: `src/genesis/onboarding/{__init__,state,facade}.py`,
  `src/genesis/ui/_onboarding_copy.py`,
  `src/genesis/ui/pages/onboarding.py`,
  `tests/test_onboarding_unit.py`. Modified:
  `src/genesis/core/facade.py` (the lazy `facade.onboarding`
  property), `src/genesis/ui/app.py` (the nav gate),
  `src/genesis/ui/pages/settings.py` (the Setup expander),
  `src/genesis/users/store.py` (the account-creation hook),
  `tests/test_users_unit.py`, `README.md`, and ADR-032.
- **The project step's draft.** The draft holds two keys:
  `projects` (the ticked names) and `project_names` (the names on
  the step, the starters plus the ones added on it). The plan named
  one key; two are needed to keep an added name on the step after a
  refresh while still remembering that it was unticked.
- **When the projects are created.** On leaving the step, Next or
  Back, matching ADR-032 section 9.
- **The profile and life story steps** write on Save, and on Next or
  Back when the text differs from what is stored, as planned.
- **The account-creation hook** runs after the users-table write and
  the htpasswd write have both succeeded, as planned. A failure is
  logged and the account is created anyway.
- **The users test fixture** also patches `XDG_DATA_HOME`, so the
  htpasswd write in `create_user` does not land in the developer's
  real home either.
- **Verification.** The unit suite covers the state machine. The
  wizard itself was walked end to end with Streamlit's `AppTest`
  harness during development (all seven steps, the restart, and the
  legacy-account case); the repository keeps no Streamlit test
  harness, so the manual smoke test in the QA plan above remains the
  acceptance check.
