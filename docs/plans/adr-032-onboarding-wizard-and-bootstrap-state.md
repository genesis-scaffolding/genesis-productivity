# Plan (round 3): onboarding wizard for Genesis Productivity

Round 3 is the rework of round 2 that came out of the PR gate on
5 Nov 2026. Gen browser-tested the round-2 wizard: the state machine
worked, the wording and the step layouts did not. The disposition
was "fix in place": the plan held, the prose and the layouts change.

This document supersedes round 2. The ADR is at
`docs/adr/adr-032-onboarding-wizard-and-bootstrap-state.md`
(Status: Proposed).

---

## 0. What the rework changes

| Area | Round 2 | Round 3 | Driver |
|---|---|---|---|
| Step count | 7 steps | **6 steps** | feedback: drop the life story |
| Step order | purpose, profile, life story, projects, tasks, journals, contacts | **purpose, profile, projects, tasks, journals, contacts** | the life story step is gone |
| Prose | verbose, written in an assistant's voice | **short, plain sentences** | feedback: the writing is too verbose and "AI" |
| Entity lists | the tasks table; ad-hoc lists elsewhere | **the app's own tables: projects, tasks, journal, contacts** | feedback: use the existing tables |
| Actions column | visible | **hidden on every wizard table** | feedback: the user must not be able to escape the wizard from a table |
| Create buttons | wizard-only forms (journal textarea, contact form, project checkboxes) | **the app's own create dialogs** | feedback: reuse the existing forms |
| Projects step | preselected starter checkboxes, created on leaving the step | **the projects table and a "New Project" button** | feedback: the layout of the step |
| Starter projects | `Personal` and `Learning` created by the wizard | **`Personal` and `Learning` suggested in the copy** | feedback: "suggest the user create" |
| State schema | `draft` held the ticked project names | **no `draft`** | the only consumer was the project checkboxes |

Everything else stands: the `genesis.onboarding` module, the durable
per-user JSON state, the step surviving `st.rerun()`, the wizard
owning the session while the bootstrap is pending, no nav and no
sign-out while pending, no trace in the nav once terminal, the
restart control on the Settings page, the legacy rule keyed off the
state file's existence, and the out-of-scope list (no API endpoint,
no CLI, the state file is not in the backup archive).

---

## 1. Where I agree, and where I push back

Nothing in the feedback is infeasible. Two points needed a reading,
and both are recorded here.

### 1.1 "The lists must use the app's existing tables"

The tasks table is already shared (`render_tasks_table` in
`genesis.ui._table`). The journal table and the contacts table were
page-local: they lived in `pages/journal.py` and `pages/contacts.py`
as inline `st.data_editor` calls. They are now shared components
(`render_journal_table`, `render_contacts_table`) and the pages use
them, so there is one journal table and one contacts table in the app.

There was no projects table at all: the Projects page shows a card
grid. A shared `render_projects_table` was added next to the tasks
table, with the same shape (inline edits on the writable columns, an
optional Actions column). The Projects page keeps its card grid; the
wizard uses the table.

Every shared table hides its Actions column when the caller does not
supply an action handler. That is what the wizard does: its lists are
a view of the account, and an Open or Delete button in them is a way
out of the wizard.

### 1.2 "The create buttons must open a dialog with the existing form"

The task and project create forms were already dialogs
(`task_create_dialog`, `project_create_dialog`). The contact and the
journal forms were page views with their own forms, so they had to
become dialogs.

To avoid a parallel form, the form itself was lifted out of the page
and into `genesis.ui._render`:

- `contact_create_form` — the Contacts page's create view and the new
  `contact_create_dialog` both render it.
- `journal_create_form` — the Journal page's create view and the new
  `journal_create_dialog` both render it.

The pages keep their own chrome (the Back button, the query-param
routing); the dialogs get the same form without it. One form, two
callers.

---

## 2. The plan

### Goal

A new account is walked through Genesis Productivity once, and ends
the tour holding its own content: a profile written, a project, a
task, a journal entry, and a contact, each created through the same
form the user will use later. While the bootstrap is outstanding the
wizard owns the session: no nav, no way out but forward or Skip.
Once it is terminal the nav is exactly the nav it was before, and the
wizard is available again only by asking for it in Settings.

### Approach

1. A domain module `genesis.onboarding` owns the durable bootstrap
   state. It holds no UI code and is unit-testable without Streamlit.
2. The state lives in one per-user JSON file at
   `<XDG_STATE_HOME>/genesis/<user>/onboarding.json`.
3. `app.py` decides the nav shape from that state on every script run:
   pending -> one hidden page, no nav; terminal -> the normal nav,
   with the onboarding page registered but hidden.
4. The current step is persisted in the state file, so a refresh or the
   `st.rerun()` at the end of the reused task dialog does not lose the
   user's place.
5. The state file is written when the account is created, so "no state
   file" is a reliable "this account predates the wizard" verdict.
6. Every list is the app's own table and every create button is the
   app's own dialog. The wizard's only write of its own is the
   profile.

### Files and areas touched

#### New

| File | Contents |
|---|---|
| `src/genesis/onboarding/__init__.py` | Re-export `OnboardingFacade`, `OnboardingState`, `OnboardingStatus`, `OnboardingStep`, `STEP_ORDER`, `STATE_FILENAME`. |
| `src/genesis/onboarding/state.py` | `OnboardingStatus` (StrEnum), `OnboardingStep` (StrEnum), `STEP_ORDER`, `OnboardingState` (pydantic), `read_state(path)`, `write_state(path, state)` (atomic, mode 0600). |
| `src/genesis/onboarding/facade.py` | `OnboardingFacade`: the only entry point. |
| `src/genesis/ui/pages/onboarding.py` | The wizard page: the six steps, the terminal summary. |
| `src/genesis/ui/_onboarding_copy.py` | The wizard prose and the profile prompt. |
| `tests/test_onboarding_unit.py` | Unit tests for the state machine (fast, no server, no Streamlit). |

#### Modified

| File | Change |
|---|---|
| `src/genesis/core/facade.py` | The lazy `onboarding` property: `OnboardingFacade(self._configs.paths.state / STATE_FILENAME)`. |
| `src/genesis/ui/app.py` | Read the onboarding state; build one of two navs; suppress the sign-out control while pending; register the onboarding page as `visibility="hidden"` in the terminal nav. |
| `src/genesis/ui/pages/settings.py` | A "Setup" expander: the current state, and a "Run setup again" button that calls `restart()` and then `st.switch_page("pages/onboarding.py")`. |
| `src/genesis/users/store.py` | `create_user` writes the initial onboarding state (`not_started`) for the new account, best effort. |
| `src/genesis/ui/_table.py` | `render_tasks_table` and `render_filterable_tasks_table` take `show_actions`; new `render_projects_table`, `render_journal_table`, `render_contacts_table`. |
| `src/genesis/ui/_render.py` | New `contact_create_form`, `contact_create_dialog`, `journal_create_form`, `journal_create_dialog`. |
| `src/genesis/ui/pages/contacts.py` | Uses the shared contacts table and the shared contact form. |
| `src/genesis/ui/pages/journal.py` | Uses the shared journal table and the shared journal form. |
| `tests/test_users_unit.py` | Patch `XDG_STATE_HOME` / `XDG_CONFIG_HOME` / `XDG_DATA_HOME` in the `store` fixture. A test that `create_user` produces a `not_started` state file. |
| `README.md` | The paragraph about the first-run wizard and the Settings control. |

Not touched: `src/genesis/ui/pages/dashboard.py`, `src/genesis/ui/_profile_page.py`,
`src/genesis/profile/*`, the API, the CLI's other commands, the backup
archive.

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
    PURPOSE = "purpose"
    PROFILE = "profile"
    PROJECTS = "projects"
    TASKS = "tasks"
    JOURNALS = "journals"
    CONTACTS = "contacts"

STEP_ORDER: tuple[OnboardingStep, ...] = (
    OnboardingStep.PURPOSE,
    OnboardingStep.PROFILE,
    OnboardingStep.PROJECTS,
    OnboardingStep.TASKS,
    OnboardingStep.JOURNALS,
    OnboardingStep.CONTACTS,
)
```

`OnboardingState` holds `status`, `current_step`, `started_at`,
`updated_at`, `completed_at`, `skipped_at`, and `attempts`.

`read_state(path)`:
- the file is missing -> `OnboardingState(status=SKIPPED)`. A missing
  file means the account predates the wizard; the account is terminal
  and is never interrupted.
- the file is unreadable or invalid JSON -> log a warning and return
  the same default. A broken state file must never lock a user out.

`write_state(path, state)`: `path.parent.mkdir(parents=True,
exist_ok=True)`, then `tempfile.mkstemp` in the same directory, write
`state.model_dump_json(indent=2)`, `os.chmod(tmp, 0o600)`,
`os.replace(tmp, path)`, unlink the temp file on failure. The pattern
comes from `src/genesis/users/store.py`.

**2. `src/genesis/onboarding/facade.py`**

`OnboardingFacade(state_path: Path)`:
- `state_path` property.
- `state() -> OnboardingState`: the file's contents, or the
  "predates the wizard" default.
- `is_pending() -> bool`: `not state().is_terminal`.
- `initialise() -> OnboardingState`: writes `not_started` with
  `started_at=now`. Does not overwrite an existing file.
- `start(step=STEP_ORDER[0])`: `not_started` -> `in_progress`, sets
  `started_at` (first time only), `attempts += 1`,
  `current_step=step`.
- `set_step(step)`.
- `complete()`: `completed`, `completed_at=now`, `current_step=None`.
- `skip()`: `skipped`, `skipped_at=now`, `current_step=None`.
- `restart()`: `in_progress`, `current_step=STEP_ORDER[0]`,
  `completed_at=None`, `skipped_at=None`, `started_at=now`,
  `attempts += 1`.
- Every mutation sets `updated_at=now(UTC)` and writes the file.
  Timestamps are UTC (`datetime.now(UTC)`).

**3. `src/genesis/core/facade.py`**: wire `facade.onboarding` (lazy
property, `TYPE_CHECKING` import, one-way: `genesis.onboarding` must
not import `genesis.core`).

**4. `src/genesis/users/store.py`**: the account-creation hook.

```python
def _write_initial_onboarding_state(username: str) -> None:
    """Give a brand-new account its onboarding state file."""
    configs = Configs(yaml_path=resolve_config_path(username))
    OnboardingFacade(configs.paths.state / STATE_FILENAME).initialise()
```

Called from `create_user` after the users-table write and the htpasswd
write succeed, inside a `try/except Exception` that logs a warning and
continues. A failure must not stop the account from being created: the
account then has no state file, is treated as terminal, and can still
be given the wizard from Settings.

**5. `src/genesis/ui/app.py`**: the nav gate.

```python
def _page(path, title, icon, url_path, *, visibility="visible") -> st.Page:
    return st.Page(str(path), title=title, icon=icon, url_path=url_path, visibility=visibility)

# after rehydrate_session() and the existing _username block:

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
ensure_server=False)`. Reading one small local file needs no DAV
server, so the nav peek stays cheap.

**6. `src/genesis/ui/_table.py`**: the shared tables.

- `render_tasks_table(..., show_actions: bool = True)`: the Actions
  column is dropped from the dataframe and the column config when it
  is False. `render_filterable_tasks_table` forwards the flag.
- `render_projects_table(projects, *, caldav, key, on_action=None)`:
  Name, Description, Start, Deadline, Status, and the task count.
  Name, Description, and Status are editable inline; the dates are
  read-only (the project edit dialog owns them). The Actions column
  appears only when `on_action` is supplied.
- `render_journal_table(entries, *, key, on_action=None)`: the journal
  page's table, lifted out of the page. The Title column is editable
  inline.
- `render_contacts_table(contacts, *, key, on_action=None)`: the
  contacts page's table, lifted out of the page. Name and
  Organization are editable inline.

**7. `src/genesis/ui/_render.py`**: the shared forms and dialogs.

- `contact_create_form(*, uid, key_prefix, on_created=None,
  on_cancel=None)`: the Contacts page's form. `on_created` runs with
  the new contact; `on_cancel` runs when the user cancels.
- `contact_create_dialog()`: a dialog around that form.
- `journal_create_form(*, uid, key_prefix, default_type="daily",
  on_created=None, on_cancel=None)`: the Journal page's form. It keeps
  the one-entry-per-period rule: a second entry for a period is
  refused with the existing message.
- `journal_create_dialog(*, default_type="daily")`: a dialog around
  that form.

**8. `src/genesis/ui/_onboarding_copy.py`**: the prose. One title and
one markdown body per step, plus the profile prompt. The substance
is fixed by the feedback; the wording is short and plain.

- **purpose**: welcome; what Genesis Productivity is (it keeps track
  of what you want to do, when, and why; it records how you spend your
  time; it can turn self-improvement into a lifelong game); the four
  things it manages (commitments, plans and reflections, people,
  time); that it is designed for the user first and offers the
  smoothest support for their own AI assistant; and the purpose of
  the onboarding.
- **profile**: "First, would you like to share a bit about yourself?";
  the profile is what the agent reads (what to call you, background,
  preferences); write as much or as little as you are comfortable with;
  the data stays on the server where Genesis Productivity runs and is
  shared with the AI assistant.
- **projects**: a project is a desired outcome (a holiday, a kitchen)
  or a large ongoing activity with no definite outcome (learning); an
  envelope holding several smaller activities; working on the
  activities makes progress, and when they are done the project
  concludes; `Personal` and `Learning` are worth starting with.
- **tasks**: a task is one specific commitment and may have a
  deadline; a calendar appointment is a task too, with a scheduled
  start; the assigned date is the decision to act, independent of the
  deadline, with the "due Friday, assigned Wednesday" example.
- **journals**: long-form notes that are not tasks; daily plan and
  reflection, long-term plans, thoughts about a task or a project; as
  simple or as elaborate as you like; daily, weekly, monthly, yearly,
  and misc; one entry per period, and a second entry for a period is
  refused rather than overwritten.
- **contacts**: a contact book, as sophisticated as you want; a
  contact can be related to tasks, so you can build a CRM, or it can
  just be a list of the people you know.

**9. `src/genesis/ui/pages/onboarding.py`**

```
facade = get_facade()
onboarding = facade.onboarding
state = onboarding.state()

if state.is_terminal:
    _render_terminal(state)      # the summary screen
else:
    _render_wizard(state)        # the six steps
```

The terminal screen: the title "Setup", `st.success("Setup complete.")`
or `st.info("Setup skipped.")`, the completion timestamp, a one-line
recap of what the account now holds, a `st.page_link` to the
dashboard, and a line saying the wizard can be run again from
Settings.

The wizard:

- If the status is `not_started`, call `onboarding.start()` first.
- The step comes from `state.current_step`, never from a local
  variable. `STEP_ORDER.index(step)` gives the progress
  (`st.progress((idx + 1) / len(STEP_ORDER), text=f"Step {idx + 1} of
  {len(STEP_ORDER)}")`).
- The step body, then the footer: Back (disabled on the first step),
  Next (labelled "Finish" on the last step), and a "Skip setup"
  control on the right that calls `onboarding.skip()` and reruns.

Step bodies:

- **profile**: the copy, a `st.text_area` seeded from
  `facade.profile.read(target)`, a "Save" button ->
  `facade.profile.write(target, text, note="onboarding wizard")`, and
  on Next (and on Back) the same write if the text differs from what
  is stored, so the user cannot lose what they typed by moving on. If
  the file is empty, a warning: "Your profile is still empty. Your
  agent will have no context about you." Next is not blocked.
- **purpose**: copy only.
- **projects**: the copy, the projects table (Actions column hidden),
  and a "+ New Project" button that opens `project_create_dialog()`.
- **tasks**: the copy, a "+ Add Task" button that calls the existing
  `task_create_dialog()`, and the tasks table with
  `show_actions=False`.
- **journals**: the copy, a "+ New Entry" button that calls
  `journal_create_dialog()`, and the journal table.
- **contacts**: the copy, a "+ New Contact" button that calls
  `contact_create_dialog()`, and the contacts table.

Widget keys, all prefixed `ob_`: `ob_profile_text`, `ob_profile_save`,
`ob_add_project`, `ob_projects`, `ob_add_task`, `ob_tasks`,
`ob_add_entry`, `ob_journal`, `ob_add_contact`, `ob_contacts`,
`ob_back` + `ob_next` + `ob_skip`. No overlap with the `_ntd_*` keys
inside `task_create_dialog` or the `_cpd_*` keys inside
`project_create_dialog`.

**10. `src/genesis/ui/pages/settings.py`**: the reset control.

A fourth expander, "Setup", after the notifications expander:

- the current state, read through `facade.onboarding.state()`:
  "Not started", "In progress (step N of 6)", "Completed on <date>",
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

**11. `tests/test_onboarding_unit.py`** (fast, no server, no
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
  first step, bumps `attempts`, and clears `completed_at` /
  `skipped_at`;
- `start()` from `not_started` sets `in_progress` and the first step;
- `set_step()` round trip;
- a failed write leaves no temp file behind;
- two state paths do not share state;
- `STEP_ORDER` has the six steps in the agreed order and
  `STEP_ORDER[0] is OnboardingStep.PURPOSE` (purpose first, per the
  owner's 4 Nov adjustment);
- the life story is not a step any more.

**12. `tests/test_users_unit.py`**: patch the XDG homes in the `store`
fixture (so the new write in `create_user` does not touch the
developer's real home), and add a test that `create_user` leaves a
`not_started` onboarding state for the new account and that
`delete_user` does not remove it.

**13. `README.md`**: the paragraph about the first-run wizard and the
Settings control.

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
   1 of 6. The sidebar shows **no nav at all** and no sign-out button;
   the wizard is the only thing on screen.
2. **Purpose step.** The copy renders. Press Next.
3. **Profile step.** The copy renders, the textarea is empty, the
   warning about the empty profile shows. Type a profile, press Save:
   "Saved." appears. Press Next: the step moves to the projects step
   and the file is written under
   `<data>/genesis/<user>/profile/profile.md`.
4. **Projects step.** The copy renders. The table says "No projects to
   show." Press "+ New Project": the app's own project dialog opens.
   Create "Personal". The table shows it. Press Next.
5. **Tasks step.** Press "+ Add Task": the app's own task dialog
   opens. Add a task. The task lands in the table and the wizard stays
   on the Tasks step (the dialog's `st.rerun()` must not reset the
   step). The table has no Actions column.
6. **Journals step.** Press "+ New Entry": the app's own journal
   dialog opens. Write today's daily entry. The table shows it.
   Press "+ New Entry" again and save: the duplicate is refused with
   a readable message.
7. **Contacts step.** Press "+ New Contact": the app's own contact
   dialog opens. Add a contact. The table shows it. Press Finish.
8. **Terminal.** The normal nav is back (Productivity / User /
   Account with Settings). There is no Setup entry anywhere in the nav
   bar. The terminal summary is shown with a link to the dashboard.
9. **No trace.** Reload, navigate to every page: the wizard never
   appears, the nav never shows it.
10. **Restart from Settings.** Settings -> Setup -> "Run setup again".
    The wizard takes over the session, the nav disappears again, and
    it starts at step 1. The existing profile, projects, task,
    journal entry, and contact are all still there. Press Finish: the
    nav comes back.
11. **Legacy account.** Sign in as an account that predates the wizard
    (no state file): the nav is the normal nav, no wizard, no banner,
    no lock. Open Settings -> Setup: it says "Not run for this
    account", and "Run setup again" gives the wizard.
12. **Escape attempts while pending.** While the wizard is running:
    type `/dashboard` in the URL bar: the wizard is still the only
    page that renders (the browser shows a "page not found" message
    first). The browser Back button does not reach another page. The
    tables' Actions columns are gone, so there is no in-page way out
    either.
13. **Two accounts.** Sign out, sign in as a second, brand-new account:
    that account gets its own wizard; the first account's state is
    untouched.

### Risks

- **`st.rerun()` from the reused dialogs.** The wizard's step must
  come from the durable state, not a local variable. Handled by design;
  covered by smoke step 5.
- **Widget key collisions** between the wizard and the reused dialogs.
  All wizard keys are `ob_`-prefixed; `task_create_dialog` uses
  `_ntd_*`, `project_create_dialog` uses `_cpd_*`, the contact dialog
  uses `_ccd_*`, and the journal dialog uses `_jcd_*`.
- **`st.session_state` mutation after widget creation.** Streamlit
  raises when a widget key is set after its widget is instantiated.
  Seed with `setdefault` before creating the widget.
- **The onboarding page stays routable after the wizard is done.** It
  is hidden from the nav but answers at `/onboarding`. This is
  deliberate (it is what makes the terminal summary and the Settings
  hand-off work without a "page not found" toast).
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
- **An account created outside `create_user`** (hand-edited
  users.yaml, an old installation) is never offered the wizard
  automatically. Settings is the way in.
- **Radicale unreachable during the wizard.** The wizard's lists are
  the same reads the real pages make; a failure surfaces as an error on
  the step. The state file is unaffected, so the wizard resumes.
- **The state file is not in the backup archive** (ADR-024). Restoring
  onto an account leaves the wizard pending. Deliberate, recorded as
  out of scope in the ADR.
- **No Streamlit test harness** exists, so the UI is verified by the
  manual smoke test. This matches the precedent in
  `docs/plans/dialog-direct-invocation.md`. All logic that can be
  tested outside Streamlit lives in `genesis.onboarding` and is covered
  by `tests/test_onboarding_unit.py`.

### Open questions for the coordinator

None. The feedback was applied as written.

---

## 4. Implementation record

Implemented on branch `se/t-bqekub-onboarding-wizard`.

- **The project step lost the starter checkboxes.** The round-2 plan
  had the wizard create `Personal` and `Learning` when the user left
  the step. The feedback describes the step as the table plus a
  create button, and says to *suggest* the two names, so the wizard
  no longer creates projects on the user's behalf. The two names are in
  the step's copy.
- **The `draft` field is gone.** It only ever held the ticked project
  names. `OnboardingState` and `OnboardingFacade` no longer have it.
- **The journal and contacts tables moved.** They were inline
  `st.data_editor` calls in their pages; they are now
  `render_journal_table` and `render_contacts_table` in
  `genesis.ui._table`, and the pages call them with their own action
  handlers.
- **The contact and journal forms moved.** They are now
  `contact_create_form` and `journal_create_form` in
  `genesis.ui._render`, called by both the pages and the new dialogs.
- **A projects table was added.** There was none; the Projects page
  shows a card grid and still does.
- **The users test fixture** also patches `XDG_DATA_HOME`, so the
  htpasswd write in `create_user` does not land in the developer's
  real home either.
- **Verification.** The unit suite covers the state machine. The
  wizard was walked end to end with Streamlit's `AppTest` harness
  during development (all six steps, the restart, and the
  legacy-account case); the repository keeps no Streamlit test
  harness, so the manual smoke test above remains the acceptance
  check.
