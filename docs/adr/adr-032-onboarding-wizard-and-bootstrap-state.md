# 032. Guided onboarding wizard and durable bootstrap state

## Context

The app ships a full set of nav pages and no first-run path. A new
account opens
on an empty shell: no explanation of what a project is, no explanation of
why a task can be an appointment and a deadline at the same time, and no
mention of the journal's one-entry-per-period rule. The features that
make this system different from a plain to-do list stay invisible until
the user happens to read the docs.

The Genesis Infrastructure Toolkit hit the same problem and solved it
with a guided onboarding wizard driven by a durable state file. That flow
is the reference for this task. Its shape is worth keeping: a flow that
walks the user through the parts of the system, a state file that records
the outcome, terminal states that do not ask again, and an explicit rerun
control.

The first draft of this ADR put the wizard in the nav, as a Setup group
first and later under Account. That shape was rejected: a user who
wanders off half way through the wizard leaves the account half
bootstrapped, and the wizard's own output (projects, tasks, journal
entries) is what the rest of the app reads. So the wizard must own the
session until it reaches a terminal state.

Constraints that shape the design here:

- The UI is a multipage Streamlit app whose nav is rebuilt on every
  script run (ADR-013). The nav can already be conditioned on per-user
  state: the Character page appears only when the game layer is on. The
  same lever works for the onboarding page.
- Streamlit session state is not durable. It dies on a browser refresh,
  a new tab, and an app restart. The fact that a user has already had
  the bootstrap must outlive all three.
- The repo has no Streamlit UI test harness. Every existing unit test
  covers a module that can be exercised without a script run. The state
  machine therefore has to live outside the UI layer to be testable at
  all (ADR-011).
- Accounts are per user (ADR-019). Two people can share a machine and
  each has their own data. The bootstrap state must be per user too.
- Every step of the wizard must reuse the app's own tables and the
  app's own create forms. A parallel flow would drift from the real one
  within a week.
- The system already stores a markdown profile per account (ADR-025).
  It is the context an assistant needs before it can be useful, so the
  wizard collects it early.
- An account that predates this feature must not be interrupted. The
  wizard hides the whole nav, so a wrong "this account is new" verdict
  locks an existing user into a page they cannot leave.

## Decision

### 1. A new `genesis.onboarding` module owns the durable state

We will add a package `src/genesis/onboarding/`:

```
src/genesis/onboarding/
  __init__.py   re-export OnboardingFacade, OnboardingState, OnboardingStatus
  state.py      OnboardingStatus, OnboardingStep, STEP_ORDER, read_state, write_state
  facade.py     OnboardingFacade: the only entry point callers use
```

`OnboardingFacade` is wired onto the main facade as `facade.onboarding`,
next to `facade.profile` and `facade.game`. The facade is constructed
lazily and reads a single JSON file.

The module takes the state file path as a constructor argument and
depends only on the standard library plus pydantic. It does not import
`genesis.configs`, `genesis.ui`, `genesis.caldav`, `genesis.carddav`, or
`genesis.journal`. The core facade builds the path from the config, and
the wizard page calls the caldav, carddav, journal, and profile facades
itself.

### 2. The state lives in one per-user JSON file

The file lives at:

```text
<state_home>/genesis/<user>/onboarding.json
```

That is `configs.paths.state` plus a file name. The state home already
holds genesis runtime state: the users table lives at
`<state_home>/genesis/users.yaml`, and the per-user state directory is
`<state_home>/genesis/<user>/`. The onboarding file is per user, so one
account's bootstrap never affects another's.

The file is written atomically: a temp file in the same directory, then
`os.replace`. This matches the write pattern already used by the users
table in `genesis.users.store`.

The schema:

| Field            | Meaning                                                    |
|------------------|------------------------------------------------------------|
| `status`         | `not_started`, `in_progress`, `completed`, or `skipped`     |
| `current_step`   | Step id the wizard resumes at, or `null`                   |
| `started_at`     | When the wizard was first opened                            |
| `updated_at`     | When the state last changed                                 |
| `completed_at`   | When the wizard was completed                               |
| `skipped_at`     | When the wizard was skipped                                 |
| `attempts`       | How many times the wizard has been started                  |

A missing file reads as `skipped`, and so does a corrupt one (the read
logs a warning). A missing file means the account predates the wizard;
a corrupt file means the state cannot be trusted. Both are terminal on
purpose: a broken state file must never lock a user out of the app,
and the Settings control is the way back in.

### 3. `completed` and `skipped` are terminal

`OnboardingState.is_terminal` is true for `completed` and `skipped`.
`OnboardingFacade.is_pending` is the single gate the rest of the app
asks, and it is the negation of `is_terminal`.

Skipping is therefore treated exactly like completing. The system does
not ask again. The two statuses are kept apart only so the Settings
page can report what happened.

`restart` moves a terminal state back to `in_progress`, resets the step
to the first one, clears the completion timestamps, and increments
`attempts`. It is the only way back into the wizard.

### 4. While the bootstrap is pending, the wizard owns the session

`st.navigation` is called with two different shapes, decided on every
script run by the per-user onboarding state.

While the state is pending, the nav is:

```python
st.navigation({"Setup": [onboarding_page]}, position="hidden").run()
```

Three things follow from that one call.

- The navigation widget is not rendered at all (`position="hidden"`).
  There is no Setup group, no sidebar entry, nothing to click.
- The onboarding page is the only page in the registry, so no other page
  can be reached, not by a link and not by typing a URL. A URL for
  another page resolves to "page not found" and the wizard runs.
- The onboarding page is the default page, so the app opens on it.

The sign-out control in the sidebar is suppressed while the state is
pending, so the wizard is the only thing on screen. The wizard's own
"Skip setup" control is the way out, and it is on every step.

No `st.switch_page` call exists on any pending path, because
`st.switch_page` raises when the target page is not in the registry and
the registry holds one page while the wizard runs.

### 5. Once terminal, the wizard leaves no trace in the nav

When the state is terminal, the nav is the normal one: Productivity,
User, and Account with Settings in it. There is no Setup group and no
Setup page.

The onboarding page stays in the registry with `st.Page(...,
visibility="hidden")`. A hidden page is not rendered in the nav (the
frontend filters hidden pages out before the nav is built), so the nav
bar shows nothing, but the page's url path keeps resolving. That is what
lets the wizard show its terminal summary without the app bouncing the
user through a "page not found" redirect, and it is what lets the
Settings control hand the session back to the wizard.

The terminal state of the wizard (completed or skipped) is what restores
the normal nav. Nothing else does.

### 6. The wizard is a dedicated page with six steps

We will add `src/genesis/ui/pages/onboarding.py`.

The step order is:

1. `purpose` — what Genesis Productivity is for
2. `profile` — a bit about yourself
3. `projects` — what a project is
4. `tasks` — what a task is
5. `journals` — the journal
6. `contacts` — the contact book

The purpose step comes first: it frames the system the six steps are
about, so the profile lands on someone who already knows what the
account is for. The profile follows it because it is what the
assistant reads next, and an account that has told the system who it
is serving is worth more than an empty one.

An earlier draft of this ADR had a seventh step, the life story. It
was dropped: the life story asks a new user for a narrative history
before they have seen the tool, which is too much to ask at the
start. The Life Story page and its file stay; the wizard does not
push them.

The profile step writes through `facade.profile`, the same facade the
Profile page uses, so the file format, the snapshot history, and the
later edits are the same ones. The step shows the current content,
lets the user edit it, and writes it back on Save or on Next. Content
that is already stored and unchanged is not rewritten.

The purpose, projects, tasks, journals, and contacts steps keep the
substance they had before this decision: what the kinds of record are
for, what a project is, what a task is and what the three dates on it
mean, how the journal is anchored to a period, and what the contact
book is for.

### 7. The current step is durable

The current step is written to the state file every time it changes.

This matters because the reused task dialog ends with `st.rerun()`. A
wizard that kept the step in a local variable would jump back to the
first step every time a task was created. Reading the step from the
state file also means a browser refresh resumes where the user was.

### 8. The wizard reuses the app's own tables and forms

The wizard shows the account's own data with the app's own table
components, and creates new data with the app's own create forms. No
wizard-only table and no wizard-only form exists.

- The entity lists are the shared tables in `genesis.ui._table`:
  `render_projects_table`, `render_tasks_table`,
  `render_journal_table`, and `render_contacts_table`. A table hides
  its Actions column when the caller does not supply an action
  handler, which is what the wizard does: the lists are a view of
  the account, not a way out of the wizard.
- The create buttons open the app's create dialogs:
  `project_create_dialog` and `task_create_dialog` from
  `genesis.ui._render`, plus `contact_create_dialog` and
  `journal_create_dialog`, which wrap the same forms the Contacts
  page and the Journal page use.
- The profile is the one thing the wizard writes itself, through
  `facade.profile.write`. There is no shared profile form to reuse:
  the Profile page is a view with an edit mode and a history, not a
  form.

### 9. The project step suggests two starter projects

The project step shows the account's projects in the shared projects
table and offers one button, "New Project", which opens the same
create-project dialog the Projects page uses. The copy suggests two
projects worth starting with, `Personal` for personal tasks and
`Learning` for learning tasks.

The wizard does not create projects behind the user's back. The
suggestion is in the copy; the creation is the user's, through the
same form they will use later.

An earlier draft of this ADR had the project step preselect two
starter projects in a checkbox list and create them when the user
left the step. That was dropped: the step's layout is now the table
and the create button, like every other entity step, and a second
way to create a project is a second thing to learn and to keep in
sync with the real flow.

### 10. A missing state file means the account predates the wizard

The onboarding state file is written when the account is created. The
account creation path is `genesis.users.create_user`, which is the one
function both the signup form and the CLI call, so it is the one place
where the invariant "a new account starts in the needs-onboarding
state" can be enforced. The write is best effort: a failure is logged
and the account is created anyway.

An account with no state file is an account that predates this feature.
It is treated as terminal: the wizard never appears, the nav is never
suppressed, and the account is never interrupted. It can still be given
the wizard, but only by asking for it in Settings.

This replaces the first draft's data probe, which asked whether the
account already held projects, tasks, journal entries, or contacts and
auto-skipped the account if it did. The probe had to run against a live
DAV server on the app's nav path, and its failure mode was the one thing
we cannot afford: a wrong verdict locks an existing user into a page
they cannot leave. The file's existence is a fact instead of an
inference, so the legacy rule cannot fail.

The consequence is that an account created outside `create_user` (a
hand-written users.yaml, an old installation) is never offered the
wizard automatically. The Settings control is the way in.

### 11. Settings owns the restart control

The Settings page gets a "Setup" section that reports the current
state and offers one button, "Run setup again".

The button calls `restart` and then hands the session to the wizard
page. `restart` sets the status to `in_progress`, moves the step back
to the first one, clears the completion timestamps, and increments
the attempt counter.

The reset clears the onboarding state only. It does not touch the
profile, the projects, the tasks, the journal entries, or the
contacts. A restart is a second tour of a populated account, not a
reset of the account.

The state file is rewritten, not deleted, so the attempt counter and
the timestamps survive.

### 12. Out of scope

- No API endpoints for the onboarding state. The wizard is a human
  surface.
- No CLI command. The state is read and written by the UI.
- The state file is not part of the backup archive (ADR-024). Restoring
  a backup onto an account leaves the wizard pending, which is the
  honest outcome: the restored data says nothing about whether that
  person has been shown the tour.

## Status

Proposed

## Consequences

Positive:

- A new account can go from empty to populated without leaving the
  wizard: the user fills the system with their own first content,
  through the app's own forms.
- The wizard cannot be escaped half way. There is no nav, no link, and
  no URL that reaches another page while the state is pending.
- The parts of the system that are easy to miss (assigned date, period
  anchoring, the project as an outcome) get explained next to the
  widget that uses them.
- The account starts with the context an assistant needs: a profile,
  written through the same facade the Profile page uses.
- The bootstrap decision survives a refresh, a new tab, and a restart,
  because it lives in a file and not in session state.
- The wizard cannot drift from the real product, because its lists and
  its create forms are the app's own components.
- The state machine is unit-testable without Streamlit.
- An account that predates the wizard can never be locked out of the
  app: the legacy rule reads a file's existence, not the account's data.

Negative:

- A new module and a new per-user file are added to the system.
- The user store now writes a file outside the users table. The
  dependency is leaf-ward (`genesis.users` to `genesis.onboarding`) and
  the write is best effort.
- The sign-out control is hidden while the wizard runs. A user who wants
  to sign out has to finish or skip the wizard first.
- A user who types another page's URL while the wizard is running gets
  a "page not found" message from the browser before the wizard runs
  again. The message is a symptom of the page not being registered, not
  a way out.
- The state file is not covered by backup or restore.

Neutral:

- Accounts that predate the wizard never see it unless they ask for it
  in Settings.
- The wizard does not create focus sessions, calendar events, or XP
  events. It stays inside the six steps it describes.
- The onboarding page stays routable at its url path after the wizard
  is done. It is hidden from the nav and shows the terminal summary.
