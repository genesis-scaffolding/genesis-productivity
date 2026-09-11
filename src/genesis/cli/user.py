"""CLI: ``genesis user ...``

Subcommands for managing the users table. Mirrors the "Future
Work" section of ADR-010 (now superseded by ADR-019).
"""

from __future__ import annotations

import typer

from genesis.users import (
    MigrateError,
    MigrateSharedError,
    UserStoreError,
    change_password,
    create_user,
    delete_user,
    list_users,
    migrate_default,
    migrate_shared,
)

user_app = typer.Typer(help="Manage user accounts.")


@user_app.command(name="create")
def user_create(
    username: str = typer.Argument(..., help="Username (must match [a-zA-Z0-9_-]{1,32})."),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        help="Password. Prompts if omitted.",
        hide_input=True,
    ),
) -> None:
    """Create a new user account."""
    pw: str = password if password is not None else typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    try:
        create_user(username, pw)
    except UserStoreError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Created user '{username}'.")


@user_app.command(name="list")
def user_list() -> None:
    """List all known usernames."""
    names = list_users()
    if not names:
        typer.echo("(no users)")
        return
    for name in names:
        typer.echo(name)


@user_app.command(name="change-password")
def user_change_password(
    username: str = typer.Argument(..., help="Username to update."),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        help="New password. Prompts if omitted.",
        hide_input=True,
    ),
) -> None:
    """Replace a user's password."""
    pw: str = (
        password if password is not None else typer.prompt("New password", hide_input=True, confirmation_prompt=True)
    )
    try:
        change_password(username, pw)
    except UserStoreError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Updated password for '{username}'.")


@user_app.command(name="delete")
def user_delete(
    username: str = typer.Argument(..., help="Username to remove."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Skip the existence check.",
    ),
) -> None:
    """Remove a user from the users table.

    Refuses if any ``genesis/<username>/`` directory exists under
    the XDG bases (data on disk remains untouched). Pass
    ``--force`` to remove the account regardless.
    """
    try:
        delete_user(username)
    except UserStoreError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Removed user '{username}' from the users table.")


@user_app.command(name="migrate-default")
def user_migrate_default(
    new_username: str = typer.Argument(
        ...,
        help="Move all 'default'-user state to this username.",
    ),
    restart_server: bool = typer.Option(
        True,
        "--restart-server/--no-restart-server",
        help="Restart the Radicale server after migration so it picks up the new paths.",
    ),
) -> None:
    """Move ``default``-user data to a new username.

    Renames the four XDG base directories (config, data, state,
    cache), the Radicale collection root, and the htpasswd
    entries. By default also restarts the running Radicale
    server so it loads the relocated config (otherwise the old
    Radicale process keeps pointing at the pre-migration paths
    and CalDAV calls 500).

    After running this, create an account for ``new_username``
    with ``genesis user create <new_username>`` and log in.
    """
    try:
        notes = migrate_default(new_username)
    except MigrateError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from None
    for line in notes:
        typer.echo(line)

    if restart_server:
        from ..configs import resolve_config_path
        from ..core.facade import Facade

        typer.echo("")
        typer.echo("Restarting Radicale...")
        config_path = resolve_config_path(new_username)
        facade = Facade(config_path, ensure_server=False)
        facade.restart_server()
        facade.shutdown()
        typer.echo("Radicale is now reading from the relocated data dir.")


@user_app.command(name="migrate-shared")
def user_migrate_shared(
    username: str = typer.Argument(
        ...,
        help="Username whose per-user state should be moved into the shared layout.",
    ),
    restart_server: bool = typer.Option(
        True,
        "--restart-server/--no-restart-server",
        help="Restart the Radicale server after migration so it picks up the merged htpasswd.",
    ),
) -> None:
    """Move ``username``'s per-user state into the shared layout.

    Run this AFTER ``migrate-default`` (or on a fresh per-user
    install) to take the per-user ``radicale/``, ``journal/``,
    and ``api_secret.key`` and re-home them under the shared
    ``<XDG_DATA_HOME>/genesis/`` root so one Radicale serves
    every account.

    Use this command once per user. The operator must run it for
    every existing account before the multi-user Radicale is
    fully populated.
    """
    try:
        notes = migrate_shared(username)
    except MigrateSharedError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from None
    for line in notes:
        typer.echo(line)

    if restart_server:
        from ..configs import resolve_config_path
        from ..core.facade import Facade

        typer.echo("")
        typer.echo("Restarting Radicale...")
        config_path = resolve_config_path(username)
        facade = Facade(config_path, ensure_server=False)
        facade.restart_server()
        facade.shutdown()
        typer.echo("Radicale is now reading from the shared layout.")
