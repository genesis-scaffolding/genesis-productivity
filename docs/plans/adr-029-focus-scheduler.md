# Plan for adr-029

## Steps

1. Create `src/genesis/focus_scheduler/__init__.py` re-exporting `FocusScheduler`, `ensure_scheduler_running`, `stop_for_user`, `stop_all`.
2. Create `src/genesis/focus_scheduler/scheduler.py` with the `FocusScheduler` class, the tick body (auto-stop + milestone lift from `focus.py`), and a `daemon=True` thread managed by `threading.Event`.
3. Create `src/genesis/focus_scheduler/runtime.py` with the per-process cache (`_schedulers` dict), `ensure_scheduler_running`, `stop_for_user`, `stop_all`, and an `atexit.register(stop_all)` at module import.
4. In `src/genesis/core/facade.py`, add `_focus_scheduler` field and `_ensure_scheduler_running()` helper, called from `__init__` after `_ensure_server_running()` (gated on the same `ensure_server` flag). Add `focus_scheduler` property as a plain read of `_focus_scheduler`. Extend `Facade.shutdown()` to call `stop_for_user(self._configs.user)` before stopping Radicale.
5. Extend `src/genesis/ui/_state.py` signal handlers to call `stop_all` after `_shutdown_all`.
6. Create `src/genesis/cli/scheduler.py` with the `start` and `status` subcommands under a `scheduler_app` Typer group.
7. Wire `scheduler_app` into `src/genesis/cli/app.py` (parallel to `server_app` and `user_app`).
8. Strip timing logic from `src/genesis/ui/pages/focus.py`: remove `_maybe_fire_milestone`, the auto-stop block in `_render_active_sessions`, and `_notify_safely` (no longer used in the page). Update the module docstring.
9. Update `README.md` with one short paragraph on the new scheduler behaviour.
10. Add `tests/test_focus_scheduler_unit.py` covering: auto-stop fires finished on win, does not fire on race loss, does not fire for future-due sessions; milestone dedup (lifted cases); PUT race loss and transient failure; idempotent start/stop; daemon flag; runtime cache hit/miss; tick error swallowing; `_tick_once` direct-call test seam.
11. Update the existing `tests/test_focus_milestone_unit.py` callers — the `_maybe_fire_milestone` function no longer exists in `focus.py`, so the file's tests should target the scheduler's tick body instead. Rename / re-home the tests into `test_focus_scheduler_unit.py` so the milestone-dedup contract is covered where the logic now lives.
12. Update `tests/test_facade_unit.py` to patch `genesis.focus_scheduler.runtime.ensure_scheduler_running` to a no-op so the existing Facade lifecycle tests stay cheap.
13. Run `make lint`, `make typecheck`, `make test` and resolve any failures. Run `make test-all` to make sure integration tests still pass.

## Notes

- File-level only. Implementation details (the exact `threading.Event` plumbing, the exact shape of the fake facade in tests) are settled during coding.
- Steps 8 and 12 must land together — the milestone tests in their current form will break the moment `_maybe_fire_milestone` is removed from `focus.py`. The re-homed tests in `test_focus_scheduler_unit.py` are the contract.
- Step 12 keeps the existing Facade unit tests cheap. The patch mirrors the `ServerManager` patch they already do, so the change is local to one file.
