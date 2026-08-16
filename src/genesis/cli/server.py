"""CLI commands for Radicale server management.

Usage:
    genesis server start
    genesis server stop
    genesis server status
    genesis server restart
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from genesis.configs import Configs
from genesis.dav.server import ServerManager, is_server_running

server_app = typer.Typer(help="Manage the Radicale DAV server.")


@server_app.command(name="start")
def start_server(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to config YAML file."),
    ] = None,
) -> None:
    """Start the Radicale server if not already running.

    Blocks until the server is ready to accept connections.
    """
    if config is not None and not config.exists():
        typer.echo(f"Config file not found: {config}", err=True)
        raise typer.Exit(1)

    configs = Configs(yaml_path=config)
    manager = ServerManager(
        dav_config=configs.dav,
        data_path=configs.paths.data / "radicale",
    )

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
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to config YAML file."),
    ] = None,
) -> None:
    """Stop the Radicale server.

    Note: This only stops a server started by this instance.
    """
    if config is not None and not config.exists():
        typer.echo(f"Config file not found: {config}", err=True)
        raise typer.Exit(1)

    configs = Configs(yaml_path=config)
    manager = ServerManager(
        dav_config=configs.dav,
        data_path=configs.paths.data / "radicale",
    )

    if not manager.is_running():
        typer.echo("Server is not running.")
        return

    typer.echo("Stopping server...")
    manager.stop()
    typer.echo("Server stopped.")


@server_app.command(name="status")
def server_status(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to config YAML file."),
    ] = None,
) -> None:
    """Check if the Radicale server is running."""
    if config is not None and not config.exists():
        typer.echo(f"Config file not found: {config}", err=True)
        raise typer.Exit(1)

    configs = Configs(yaml_path=config)

    if is_server_running(configs.dav.host, configs.dav.port):
        typer.echo(f"Server is running at {configs.dav.server_url}")
    else:
        typer.echo("Server is not running.")
        sys.exit(1)


@server_app.command(name="restart")
def restart_server(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to config YAML file."),
    ] = None,
) -> None:
    """Restart the Radicale server."""
    if config is not None and not config.exists():
        typer.echo(f"Config file not found: {config}", err=True)
        raise typer.Exit(1)

    configs = Configs(yaml_path=config)
    manager = ServerManager(
        dav_config=configs.dav,
        data_path=configs.paths.data / "radicale",
    )

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
