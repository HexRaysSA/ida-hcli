from __future__ import annotations

import rich_click as click
from rich.prompt import Confirm

from hcli.lib.api.asset import SHARED, asset
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console


@auth_command(help="Delete shared file by code.")
@click.argument("code", required=True, metavar="SHORTCODE...")
@click.option("-f", "--force", is_flag=True, help="Skip confirmation prompt")
@async_command
async def delete(code: str, force: bool) -> None:
    """Delete shared files by their shortcodes.

    You can delete a shared file by its code:

    \b
    hcli share delete ABC123
    """

    try:
        console.status("[bold blue]Getting file information...")
        file = await asset.get_shared_file_by_code(code)

        if file is None:
            console.print(f"[yellow]File not found {code}.[/yellow]")
            return

        console.print("\n[bold]File to delete:[/bold]")
        console.print(f"  Name: {file.filename}")
        console.print(f"  Code: {file.code}")
        console.print(f"  Size: {format_size(file.size)}")

        # Confirmation
        if not force:
            if not Confirm.ask(f"\n[bold red]Delete file {file.filename} [{file.code}]?[/bold red]"):
                console.print("[yellow]Deletion cancelled.[/yellow]")
                return

        console.status(f"[bold red]Deleting {file.filename} [{file.code}]...[/bold red]")
        await asset.delete_file_by_key(SHARED, file.key)
        console.print(f"[green]✓ Deleted: {code}[/green]")

    except Exception as e:
        console.print(f"[red]Error during deletion: {e}[/red]")
        raise click.Abort()


def format_size(bytes_count: int) -> str:
    """Convert bytes to human-readable format."""
    sizes = ["B", "KB", "MB", "GB", "TB"]
    if bytes_count == 0:
        return "0 B"

    import math

    i = int(math.floor(math.log(bytes_count) / math.log(1024)))
    return f"{bytes_count / math.pow(1024, i):.1f} {sizes[i]}"


def truncate(text: str, max_length: int) -> str:
    """Truncate text with ellipsis if too long."""
    return text[: max_length - 3] + "..." if len(text) > max_length else text
