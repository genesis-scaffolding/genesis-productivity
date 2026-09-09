"""CLI application using Typer."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from ..configs import CONFIG_PATH_ENV, DEFAULT_USER, resolve_config_path
from ..core.facade import Facade
from .server import server_app

app = typer.Typer(help="CLI for genesis productivity system.")
app.add_typer(server_app, name="server")

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
    """
    import subprocess
    import sys

    from ..configs import CONFIG_PATH_ENV, DEFAULT_USER, resolve_config_path

    config_path = resolve_config_path(DEFAULT_USER)
    os.environ[CONFIG_PATH_ENV] = str(config_path.resolve())

    app_path = Path(__file__).resolve().parent.parent / "ui" / "app.py"
    port = os.environ.get("GENESIS_UI_PORT", "3000")
    host = os.environ.get("GENESIS_UI_HOST", "0.0.0.0")
    return subprocess.call(
        [
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
    )


if __name__ == "__main__":
    main()
