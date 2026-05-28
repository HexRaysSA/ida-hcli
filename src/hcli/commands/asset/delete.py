from __future__ import annotations

import rich_click as click

from hcli.lib.api.asset import asset
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console


@auth_command()
@click.argument("key", type=str)
@click.option("-b", "--bucket", required=True, type=str, help="Bucket to delete from")
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation prompt",
)
@async_command
async def delete(
    key: str,
    bucket: str,
    yes: bool = False,
) -> None:
    """Delete an asset by key."""

    if not yes:
        click.confirm(
            f"Are you sure you want to delete '{key}' from bucket '{bucket}'?",
            abort=True,
        )

    await asset.delete_file_by_key(bucket=bucket, key=key)

    console.print("[green]✓ Asset deleted successfully![/green]")
    console.print(f"[bold]Bucket:[/bold] {bucket}")
    console.print(f"[bold]Key:[/bold] {key}")
