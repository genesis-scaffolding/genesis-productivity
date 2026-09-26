"""CLI commands for the focus scheduler.

Usage:

    genesis scheduler start --user <name>   # foreground, blocks
    genesis scheduler status --user <name>  # show local cache state

The ``start`` subcommand constructs a :class:`Facade` for the
configured user (which starts the scheduler thread via
``ensure_server=True``), then blocks until ``SIGINT`` or
``SIGTERM``. ``make serve`` already runs a scheduler in the
FastAPI process; this command exists for headless deployments
where the user wants notifications without running the web
stack.

The ``status`` subcommand inspects the *current process*'
runtime cache only; it does not detect schedulers running in
other processes.

ADR-029.
"""

from __future__ import annotations

import signal
import threading

import typer

from ..configs import DEFAULT_USER, resolve_config_path
from ..core.facade import Facade

scheduler_app = typer.Typer(help="Manage the focus scheduler daemon.")

_user_option = typer.Option(
    DEFAULT_USER,
    "--user",
    "-u",
    help=f"User name. Defaults to '{DEFAULT_USER}'. Determines config location.",
)


@scheduler_app.command(name="start")
def start_scheduler(
    user: str = _user_option,
) -> None:
    """Start the focus scheduler in the foreground and block.

    The scheduler thread ticks every 5 seconds. Press Ctrl+C
    (or send ``SIGTERM``) to stop. On exit, ``atexit`` joins
    the daemon thread cleanly.
    """
    config_path = resolve_config_path(user)
    facade = Facade(config_path)
    scheduler = facade.focus_scheduler
    if scheduler is None:
        # Defensive: ``ensure_server=True`` is the default, so
        # the scheduler is always set. If a future caller passes
        # ``ensure_server=False`` the property returns ``None``;
        # tell the user rather than silently exiting.
        typer.echo(
            "Focus scheduler was not started (ensure_server=False?). Nothing to do.",
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo(f"Focus scheduler running for user '{user}'. Ctrl+C to stop.")

    stop = threading.Event()

    def _handler(signum: int, frame: object) -> None:
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Signal handlers can only be installed from the main
            # thread; tests and worker contexts skip silently.
            pass

    # Poll once a second so we react to signals promptly without
    # busy-looping. The scheduler's own thread is unaffected.
    while not stop.is_set():
        stop.wait(timeout=1.0)

    typer.echo("Stopping scheduler...")
    facade.shutdown()
    typer.echo("Stopped.")


@scheduler_app.command(name="status")
def scheduler_status(
    user: str = _user_option,
) -> None:
    """Show whether a focus scheduler is running in *this* process.

    Detects the scheduler via the per-process runtime cache.
    Cross-process detection is out of scope for this round;
    other processes may host their own schedulers.
    """
    from ..focus_scheduler.runtime import _schedulers

    scheduler = _schedulers.get(user)
    if scheduler is None:
        typer.echo(f"No focus scheduler running in this process for user '{user}'.")
        raise typer.Exit(code=1)
    typer.echo(f"Focus scheduler running for user '{user}'.")
    typer.echo(f"  is_running : {scheduler.is_running}")
    typer.echo(f"  tick_count : {scheduler.tick_count}")
    typer.echo(f"  last_tick  : {scheduler.last_tick_at.isoformat() if scheduler.last_tick_at else 'never'}")
