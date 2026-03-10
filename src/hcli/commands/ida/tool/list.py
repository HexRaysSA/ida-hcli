from __future__ import annotations

from pathlib import Path

import rich_click as click
from rich.table import Table

from hcli.lib.commands import async_command
from hcli.lib.config import config_store
from hcli.lib.console import console


@click.command()
@async_command
async def list_tools() -> None:
    """List installed IDA-related utility tools."""
    installed: dict = config_store.get_object("tools.installed", {}) or {}

    if not installed:
        console.print("[yellow]No tools installed.[/yellow]")
        console.print("[blue]Use 'hcli ida tool search' to find available tools.[/blue]")
        return

    table = Table(title="Installed Tools")
    table.add_column("Name", style="cyan")
    table.add_column("Version", style="green")
    table.add_column("Path", style="blue")
    table.add_column("Status", style="bold")

    for name, info in installed.items():
        path = info.get("path", "")
        exists = Path(path).exists() if path else False
        status = "[green]OK[/green]" if exists else "[red]Missing[/red]"
        table.add_row(name, info.get("version", "unknown"), path, status)

    console.print(table)
