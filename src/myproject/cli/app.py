"""CLI application using Typer."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from ..core.facade import Facade

app = typer.Typer(help="CLI for myproject.")
_CONFIG_ENV = "MYPROJECT__CONFIG_PATH"

_config_option = typer.Option(
    None,
    "--config",
    "-c",
    help="Path to configs.yaml. Defaults to XDG path.",
)


@app.command()
def info(config_path: Path | None = _config_option) -> None:
    """Print main configuration values."""
    facade = Facade(config_path)
    cfg = facade.configs

    typer.echo(f"app_name : {cfg.app_name}")
    typer.echo(f"paths.config : {cfg.paths.config}")
    typer.echo(f"paths.data    : {cfg.paths.data}")
    typer.echo(f"paths.state   : {cfg.paths.state}")
    typer.echo(f"paths.cache   : {cfg.paths.cache}")


@app.command()
def ui(config_path: Path | None = _config_option) -> None:
    """Start the Streamlit UI."""
    # Pass config path to the UI via env var
    if config_path is not None:
        os.environ[_CONFIG_ENV] = str(config_path.resolve())

    # Launch streamlit, pointing it at the UI module
    from streamlit.web import cli as stcli

    sys.argv = ["streamlit", "run", "src/myproject/ui/app.py"]
    sys.exit(stcli.main())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
