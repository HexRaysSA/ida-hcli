from __future__ import annotations

import rich_click as click
from rich.console import Console
from rich.table import Table

from hcli.lib.config import config_store

console = Console()


@click.command()
def list_sources() -> None:
    """List all knowledge sources."""
    # Get existing sources
    sources: dict[str, str] = config_store.get_object("ke.sources", {}) or {}

    if not sources:
        console.print("[yellow]No knowledge sources configured.[/yellow]")
        return

    # Create table
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name", style="cyan", width=20)
    table.add_column("Path", style="white")

    # Add rows
    for name, path in sources.items():
        table.add_row(name, path)

    console.print(table)
