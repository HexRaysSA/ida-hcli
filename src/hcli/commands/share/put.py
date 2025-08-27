from __future__ import annotations

from pathlib import Path

import questionary
import rich_click as click
from questionary import Choice

from hcli.commands.common import safe_ask_async
from hcli.lib.api.asset import SHARED, asset, get_permissions_from_acl_type
from hcli.lib.auth import get_auth_service
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console
from hcli.lib.constants import cli
from hcli.lib.util.string import get_email_domain


@auth_command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-a",
    "--acl",
    type=click.Choice(["private", "authenticated", "domain"]),
    help="Access control level (private, authenticated, domain)",
)
@click.option("-c", "--code", help="Upload a new version for an existing code")
@click.option("-f", "--force", is_flag=True, help="Upload a new version for an existing code")
@async_command
async def put(path: Path, acl: str | None, code: str | None, force: bool) -> None:
    """Upload a shared file."""

    # Validate file exists
    if not path.exists():
        console.print(f"[red]Error: File not found: {path}[/red]")
        raise click.Abort()

    if not path.is_file():
        console.print(f"[red]Error: Path is not a file: {path}[/red]")
        raise click.Abort()

    # Get user info for domain ACL
    auth_service = get_auth_service()
    user = auth_service.get_user()
    assert user is not None, "User not found"
    domain = get_email_domain(user["email"]) if user else ""

    # Determine ACL if not provided
    if not acl:
        choices = [
            Choice("[private] Just for me", value="private"),
            Choice(f"[domain] Anyone from my domain (@{domain})", value="domain"),
            Choice("[authenticated] Anyone authenticated with the link", value="authenticated"),
        ]

        acl = await safe_ask_async(
            questionary.select("Pick a visibility 🔎", choices=choices, default="authenticated", style=cli.SELECT_STYLE)
        )

    assert acl is not None, "ACL not selected"

    # Conflicts check
    if force and code:
        console.print("[red]Error: --force and --code cannot be used together[/red]")
        raise click.Abort()

    # Upload the file
    acl_fields = get_permissions_from_acl_type(acl, user["email"])
    result = await asset.upload_asset(
        SHARED,
        str(path),
        allowed_segments=acl_fields["allowed_segments"],
        allowed_emails=acl_fields["allowed_emails"],
        metadata={"acl_type": acl},
        force=force,
        code=code,
    )

    console.print("[green]✓ File uploaded successfully![/green]")
    console.print(f"[bold]Share Code:[/bold] {result.code}")
    console.print(f"[bold]Share URL:[/bold] {result.url}")
    console.print(f"[bold]Download URL:[/bold] {result.download_url}")
