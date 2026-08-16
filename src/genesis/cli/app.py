"""CLI application using Typer."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from ..configs import DEFAULT_APP_NAME, DEFAULT_USER
from ..core.facade import Facade

app = typer.Typer(help="CLI for genesis productivity system.")
_CONFIG_ENV = "GENESIS__CONFIG_PATH"


def resolve_user_config(user: str | None) -> Path | None:
    """Resolve --user flag to a config path.

    If user is None or "default", return None (use default paths).
    If user is specified, return their config path and create dir if needed.
    """
    if user is None or user == DEFAULT_USER:
        return None

    config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    config_path = Path(config_home) / DEFAULT_APP_NAME / user / "configs.yaml"

    # Create directory if it doesn't exist
    config_path.parent.mkdir(parents=True, exist_ok=True)

    return config_path


_config_option = typer.Option(
    None,
    "--config",
    "-c",
    help="Path to configs.yaml. Overrides --user.",
)

_user_option = typer.Option(
    DEFAULT_USER,
    "--user",
    "-u",
    help=f"User name. Defaults to '{DEFAULT_USER}'. Determines config location.",
)


@app.command()
def info(
    config_path: Path | None = _config_option,
    user: str = _user_option,
) -> None:
    """Print main configuration values."""
    resolved_config = config_path or resolve_user_config(user)
    facade = Facade(resolved_config)
    cfg = facade.configs

    typer.echo(f"user         : {cfg.user}")
    typer.echo(f"app_name     : {cfg.app_name}")
    typer.echo(f"paths.config : {cfg.paths.config}")
    typer.echo(f"paths.data    : {cfg.paths.data}")
    typer.echo(f"paths.state   : {cfg.paths.state}")
    typer.echo(f"paths.cache   : {cfg.paths.cache}")


@app.command()
def ui(
    config_path: Path | None = _config_option,
    user: str = _user_option,
) -> None:
    """Start the Streamlit UI."""
    resolved_config = config_path or resolve_user_config(user)

    # Pass config path to the UI via env var
    if resolved_config is not None:
        os.environ[_CONFIG_ENV] = str(resolved_config.resolve())

    # Launch streamlit, pointing it at the UI module
    from streamlit.web import cli as stcli

    sys.argv = ["streamlit", "run", "src/genesis/ui/app.py"]
    sys.exit(stcli.main())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
