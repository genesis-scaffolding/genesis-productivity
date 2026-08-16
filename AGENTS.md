# Development Rules

This project is a productivity system that manages tasks, projects, calendar, contacts, and journal entries. Data is exposed via CalDAV/CardDAV APIs and markdown files for agentic access and human interaction via a web UI. Uses uv, ruff, pyright, pytest, and make.

## Adapting This Template

When starting a new project from this template, update these files first:

1. **README.md** — replace template description with project-specific content.
2. **AGENTS.md (line 1)** — replace the first paragraph with a one-line description of this project.

The agent gathers the project name and description by asking the developer. Do not guess or infer.

## Conversational Style

Use plain language. Short sentences. No emojis, no fluff, no filler text. Plain ASCII only. No em-dashes. Be direct and concise.

Example:

```
# Good
Thanks. The plan looks right. I will start coding.

# Bad
Sounds great! Let me get started on that for you. I'll work through each
step carefully and keep you posted on progress.
```

## Tool Chain

Use `uv` for all Python commands. Never run system Python.

```
# Correct
uv run python script.py
uv run pytest
uv run ruff check .

# Wrong
python script.py
pytest
```

Use `uv add` and `uv remove` to manage dependencies. Do not edit `pyproject.toml` by hand.

Use `make` for dev commands. Run `make help` to see available targets.

## Workflow

```
ADR -> Plan -> Code
```

### When to Write an ADR

Write an ADR only for real architectural decisions. A decision needs an ADR if it:
- Changes module boundaries or import direction
- Introduces a new pattern or tool
- Makes a commitment that is hard to reverse

Trivial changes — a new field, a renamed function, a moved file — do not need an ADR.

### When to Write a Plan

Write a plan when the change touches multiple files or involves architecture. Trivial changes: just do it and test.

### Plan Approval Before Coding

1. Agent investigates the problem and proposes questions or alternatives.
2. Agent and developer discuss until developer is satisfied.
3. Agent writes the ADR and plan.
4. Developer reviews and approves.
5. Agent codes with full autonomy until done.
6. Agent runs lint, typecheck, and tests.
7. Agent turns control to developer for final review and commit.

## ADR Conventions

Location: `docs/adr/adr-NNN-kebab-case-title.md`

NNN is sequential, zero-padded, never reused.

Structure (Nygard format):

```
# NNN. Title

## Context
Forces at play. What is the problem? What constraints exist?

## Decision
We will [what we decided]. Full sentences.

## Status
Proposed / Accepted / Superseded by adr-NNN

## Consequences
What changes as a result? Positive, negative, neutral.
```

Do not edit existing ADRs. When an ADR is replaced, mark it `Superseded by adr-NNN` and keep the file.

Before making a decision, check existing ADRs. Do not conflict with accepted decisions without a new ADR.

## Plan Conventions

Location: `docs/plans/adr-NNN-kebab-case-title.md`

A plan is a step-by-step todo list. File-level, not implementation details.

```
# Plan for adr-NNN

## Steps

1. Create `src/myproject/new_module.py`
2. Add `Facade` class with `do_the_thing()` method
3. Update `docs/architecture.md`
...
```

## Testing Conventions

Test public contracts only. A contract is the behavior a module exposes to its callers.

Follow the regression model. When something breaks, add a test so it never breaks again. Do not preemptively test.

Do not test third-party libraries.

Keep the test suite small. If nobody can tell what a test is testing, it should not exist.

Fuzzing: add when a concrete need surfaces. Not preemptively.

## Code Conventions

### Comments

Minimal. Code should explain itself. Only write comments for:
- Workarounds (with bug reference if known)
- Non-obvious "why" that cannot be inferred from code and types

### Imports

Top-level only. No inline imports.

```
# Correct
from pathlib import Path
from myproject.core import Facade

def foo():
    pass

# Wrong
def foo():
    from myproject.core import Facade
    pass
```

### Exceptions

Use built-in exceptions only. No custom exception classes.

### Error Handling

Crash loudly by default. Only silently use defaults if developer explicitly approves.

### Naming

Follow Python conventions: snake_case for functions and variables, PascalCase for classes. No abbreviations in public APIs.

## Module Boundaries

Keep modules cohesive and decoupled.

Import direction must be one-way. If A imports B, B must not import A.

Use a facade as the entry point to each module. Callers use the facade, not internal classes.

Use abstract base classes (ABC) for extension points and plugins.

Use Pydantic for data input.

## Dependencies

Developer approval is required before adding any new package.

## Docs

Agent proposes updates to architecture docs. Developer confirms.

New architectural decisions: write a new ADR. Do not edit existing ADRs.

## Git Workflow

Work on a branch off main.

```
feature/<kebab-name>   # new work
fix/<kebab-name>        # bugfixes
```

Never commit directly to main.

Commit message: one line, concise. No body.

```
# Correct
feat(core): add Facade.configs property

# Wrong
feat(core): added the configs property to the Facade class, which
returns the resolved configuration object from the loader.
```

Developer signs off before commit.

## Parallel Agent Rules

Multiple agents may work on different files in the same worktree simultaneously.

### Committing

- Only commit files you changed in this session.
- Never use `git add -A` or `git add .`. Stage specific files only.
- Run `git status` before committing. Verify you are staging only your files.
- Track which files you created, modified, or deleted.

### Forbidden

- `git reset --hard` — destroys uncommitted changes
- `git checkout .` — destroys uncommitted changes
- `git clean -fd` — deletes untracked files
- `git stash` — stashes all changes including other agents' work
- `git add -A` / `git add .` — stages other agents' uncommitted work
- `git commit --no-verify` — never allowed

### If Rebase Conflicts Occur

Resolve conflicts in your files only. If conflict is in a file you did not modify, abort and ask the developer. Never force push.

### Conflict With These Rules

If developer instructions conflict with rules here, ask for confirmation before overriding.
