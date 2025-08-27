from __future__ import annotations

from pathlib import Path

import questionary
import rich_click as click
from questionary import Choice
from rich.prompt import Confirm
from rich.table import Table

from hcli.commands.common import safe_ask_async
from hcli.lib.api.asset import SHARED, Asset, asset
from hcli.lib.api.common import get_api_client
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console
from hcli.lib.constants import cli


@auth_command()
@click.option("--limit", type=int, default=100, help="Maximum number of files to display")
@click.option("--offset", type=int, default=0, help="Offset for pagination")
@click.option(
    "--interactive/--no-interactive",
    default=True,
    help="Enable interactive mode for file operations",
)
@async_command
async def list_shares(limit: int, offset: int, interactive: bool) -> None:
    """List and manage your shared files."""

    try:
        # Get shared files
        with console.status("[bold blue]Loading shared files..."):
            from hcli.lib.api.asset import PagingFilter

            filter_params = PagingFilter(limit=limit, offset=offset)
            page = await asset.get_files(SHARED, filter_params)

        if not page.items:
            console.print("[yellow]No shared files found.[/yellow]")
            return

        if interactive:
            await interactive_file_management(page.items)
        else:
            display_files_table(page.items)

    except Exception as e:
        console.print(f"[red]Error listing files: {e}[/red]")
        raise click.Abort()


def display_files_table(files: list[Asset]) -> None:
    """Display files in a formatted table."""
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Index", style="dim", width=5)
    table.add_column("Code", style="cyan", width=10)
    table.add_column("Name", style="white", width=30)
    table.add_column("Version", style="blue", width=8)
    table.add_column("Size", style="green", width=10)
    table.add_column("Created", style="yellow", width=20)
    table.add_column("ACL", style="red", width=12)

    for i, file in enumerate(files, 1):
        name = truncate(file.filename or "unnamed", 30)
        version = f"v{file.version}"
        size = format_size(file.size)
        created = format_date_time(file.created_at) if file.created_at else "N/A"
        acl = file.metadata.get("acl_type") if file.metadata else None

        table.add_row(str(i), file.code, name, version, size, created, acl)

    console.print(table)


async def interactive_file_management(files: list[Asset]) -> None:
    """Provide interactive file management options."""

    # First, let user select files using checkbox
    file_choices = [
        Choice(title=f"{file.filename or 'unnamed'} ({file.code}) - {format_size(file.size)}", value=file)
        for file in files
    ]

    selected_files = await safe_ask_async(
        questionary.checkbox(
            "Select files to manage:",
            choices=file_choices,
            use_search_filter=True,
            use_jk_keys=False,
            style=cli.CHECKBOX_STYLE,
        )
    )

    if not selected_files:
        console.print("[yellow]No files selected.[/yellow]")
        return

    # Then let user choose action
    count = len(selected_files)
    action_choices = [
        f"Delete {count} file{'s' if count > 1 else ''}",
        f"Download {count} file{'s' if count > 1 else ''}",
    ]

    action = await safe_ask_async(
        questionary.select("What would you like to do?", choices=action_choices, style=cli.SELECT_STYLE)
    )

    if action.startswith("Delete"):
        await perform_delete_action(selected_files)
    elif action.startswith("Download"):
        await perform_download_action(selected_files)


async def perform_download_action(selected_files: list[Asset]) -> None:
    """Perform download action on selected files."""
    # Get output directory
    output_dir = await safe_ask_async(questionary.text("Output directory", default="./"))
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Download files
    client = await get_api_client()

    for file in selected_files:
        try:
            console.print(f"\n[blue]Downloading {file.filename}...[/blue]")

            # Get download URL
            file_info = await asset.get_shared_file_by_code(file.code if file.code else "", file.version)

            if not file_info:
                console.print(f"[red]✗ Failed to get download info for {file.filename}[/red]")
                continue

            # Download file
            if not file_info.url:
                console.print(f"[red]✗ No download URL available for {file.filename}[/red]")
                continue

            downloaded_path = await client.download_file(
                file_info.url,
                target_dir=output_path,
                target_filename=file.filename,
                force=False,
                auth=True,
            )

            console.print(f"[green]✓ Downloaded: {downloaded_path}[/green]")

        except Exception as e:
            console.print(f"[red]✗ Failed to download {file.filename}: {e}[/red]")

    console.print(f"\n[green]Download completed. Files saved to: {output_path}[/green]")


async def perform_delete_action(selected_files: list[Asset]) -> None:
    """Perform delete action on selected files."""
    # Confirm deletion
    console.print(f"\n[red]You are about to delete {len(selected_files)} file(s):[/red]")
    for file in selected_files:
        console.print(f"  • {file.filename} ({file.code})")

    if not Confirm.ask("\n[bold red]Are you sure you want to delete these files?[/bold red]", default=False):
        console.print("[yellow]Deletion cancelled.[/yellow]")
        return

    # Delete files
    for file in selected_files:
        try:
            await asset.delete_file_by_key(SHARED, file.key)
            console.print(f"[green]✓ Deleted: {file.filename} ({file.code})[/green]")
        except Exception as e:
            console.print(f"[red]✗ Failed to delete {file.filename}: {e}[/red]")


def format_size(bytes_count: int) -> str:
    """Convert bytes to human-readable format."""
    sizes = ["B", "KB", "MB", "GB", "TB"]
    if bytes_count == 0:
        return "0 B"

    import math

    i = int(math.floor(math.log(bytes_count) / math.log(1024)))
    return f"{bytes_count / math.pow(1024, i):.1f} {sizes[i]}"


def format_date(iso_string: str) -> str:
    """Format ISO date string to readable format."""
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return iso_string


def format_date_time(iso_string: str) -> str:
    """Format ISO date string to readable format."""
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_string


def truncate(text: str, max_length: int) -> str:
    """Truncate text with ellipsis if too long."""
    return text[: max_length - 3] + "..." if len(text) > max_length else text
