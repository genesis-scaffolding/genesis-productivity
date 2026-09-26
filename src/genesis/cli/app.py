"""CLI application using Typer."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from ..configs import CONFIG_PATH_ENV, DEFAULT_USER, resolve_config_path
from ..core.facade import Facade
from .scheduler import scheduler_app
from .server import server_app
from .user import user_app

app = typer.Typer(help="CLI for genesis productivity system.")
app.add_typer(server_app, name="server")
app.add_typer(scheduler_app, name="scheduler")
app.add_typer(user_app, name="user")

_user_option = typer.Option(
    DEFAULT_USER,
    "--user",
    "-u",
    help=f"User name. Defaults to '{DEFAULT_USER}'. Determines config location.",
)


@app.command()
def info(
    user: str = _user_option,
) -> None:
    """Print main configuration values."""
    config_path = resolve_config_path(user)
    facade = Facade(config_path)
    cfg = facade.configs

    typer.echo(f"user         : {cfg.user}")
    typer.echo(f"app_name     : {cfg.app_name}")
    typer.echo(f"paths.config : {cfg.paths.config}")
    typer.echo(f"paths.data    : {cfg.paths.data}")
    typer.echo(f"paths.state   : {cfg.paths.state}")
    typer.echo(f"paths.cache   : {cfg.paths.cache}")


@app.command()
def ui(
    user: str = _user_option,
    port: int = typer.Option(3000, "--port", "-p", help="Port for the Streamlit server."),
    host: str = typer.Option("0.0.0.0", "--host", help="Interface for the Streamlit server to bind to."),
) -> None:
    """Start the Streamlit UI."""
    config_path = resolve_config_path(user)
    os.environ[CONFIG_PATH_ENV] = str(config_path.resolve())

    # Launch streamlit, pointing it at the UI module
    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit",
        "run",
        "src/genesis/ui/app.py",
        "--server.address",
        host,
        "--server.port",
        str(port),
        "--server.headless",
        "true",
    ]
    sys.exit(stcli.main())


def main() -> None:
    app()


def main_ui() -> int:
    """Console-script entry point for ``genesis-ui``.

    Starts Streamlit for the default user. Honors ``GENESIS_UI_PORT``
    (default 3000) and ``GENESIS_UI_HOST`` (default 0.0.0.0).

    Implementation note: this entry point ``execvp``s into ``streamlit
    run`` rather than spawning it as a subprocess. The previous
    ``subprocess.call`` approach added a Python wrapper layer between
    the shell and streamlit; when the shell sent SIGINT to its
    process group (``kill 0``), the wrapper died but the streamlit
    process was reparented to PID 1 and stopped responding to the
    signal, leaving bash's ``wait`` blocked. Replacing the wrapper
    with streamlit keeps the process tree flat, so signals reach
    streamlit directly and Radicale (started by the streamlit
    worker) gets torn down via the signal handler in
    :mod:`genesis.ui._state`.
    """
    from ..configs import CONFIG_PATH_ENV, DEFAULT_USER, resolve_config_path

    config_path = resolve_config_path(DEFAULT_USER)
    os.environ[CONFIG_PATH_ENV] = str(config_path.resolve())

    app_path = Path(__file__).resolve().parent.parent / "ui" / "app.py"
    port = os.environ.get("GENESIS_UI_PORT", "3000")
    host = os.environ.get("GENESIS_UI_HOST", "0.0.0.0")

    argv = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.address",
        host,
        "--server.port",
        port,
        "--server.headless",
        "true",
    ]
    # ``execvp`` replaces this Python process with streamlit. PID
    # stays the same, so the shell's process-group signal still
    # reaches streamlit directly.
    os.execvp(sys.executable, argv)
    # Unreachable: execvp only returns on failure.
    return 1


if __name__ == "__main__":
    main()
