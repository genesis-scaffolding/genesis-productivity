"""CLI commands for Radicale server management.

Usage:
    genesis server start
    genesis server stop
    genesis server status
    genesis server restart
"""

from __future__ import annotations

import sys

import typer

from genesis.configs import DEFAULT_USER, Configs, resolve_config_path
from genesis.dav.server import ServerManager, is_server_running

server_app = typer.Typer(help="Manage the Radicale DAV server.")

_user_option = typer.Option(
    DEFAULT_USER,
    "--user",
    "-u",
    help=f"User name. Defaults to '{DEFAULT_USER}'. Determines config location.",
)


def _build_manager(user: str) -> tuple[ServerManager, Configs]:
    """Build a ServerManager pointing at the shared Radicale root.

    The ``user`` argument only matters for resolving the per-user
    configs.yaml; the Radicale data path is shared across all
    users (ADR-019 option A). The returned Configs is included
    for callers that want to print the server URL.
    """
    config_path = resolve_config_path(user)
    configs = Configs(yaml_path=config_path)
    manager = ServerManager(
        dav_config=configs.dav,
        data_path=configs.paths.radicale_root,
    )
    return manager, configs


@server_app.command(name="start")
def start_server(
    user: str = _user_option,
) -> None:
    """Start the Radicale server if not already running.

    Blocks until the server is ready to accept connections.
    """
    manager, configs = _build_manager(user)

    if manager.is_running():
        typer.echo(f"Server already running at {configs.dav.server_url}")
        return

    typer.echo(f"Starting server at {configs.dav.server_url}...")
    try:
        manager.start()
        typer.echo("Server started successfully.")
    except Exception as e:
        typer.echo(f"Failed to start server: {e}", err=True)
        sys.exit(1)


@server_app.command(name="stop")
def stop_server(
    user: str = _user_option,
) -> None:
    """Stop the Radicale server.

    Note: This only stops a server started by this instance.
    """
    manager, _ = _build_manager(user)

    if not manager.is_running():
        typer.echo("Server is not running.")
        return

    typer.echo("Stopping server...")
    manager.stop()
    typer.echo("Server stopped.")


@server_app.command(name="status")
def server_status(
    user: str = _user_option,
) -> None:
    """Check if the Radicale server is running."""
    manager, configs = _build_manager(user)

    if is_server_running(configs.dav.host, configs.dav.port):
        typer.echo(f"Server is running at {configs.dav.server_url}")
    else:
        typer.echo("Server is not running.")
        sys.exit(1)


@server_app.command(name="restart")
def restart_server(
    user: str = _user_option,
) -> None:
    """Restart the Radicale server."""
    manager, configs = _build_manager(user)

    if manager.is_running():
        typer.echo("Stopping server...")
        manager.stop()

    typer.echo("Starting server...")
    try:
        manager.start()
        typer.echo("Server restarted successfully.")
    except Exception as e:
        typer.echo(f"Failed to restart server: {e}", err=True)
        sys.exit(1)
