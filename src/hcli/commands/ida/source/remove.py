from __future__ import annotations

import rich_click as click
from rich.console import Console

from hcli.lib.config import config_store

console = Console()


@click.command()
@click.argument("name")
def remove(name: str) -> None:
    """Remove a named source.

    NAME: Source name to remove (as shown in 'hcli ida source list').
    """
    sources: dict[str, str] = config_store.get_object("idb.sources", {}) or {}

    if name not in sources:
        console.print(f"[yellow]Source not found: '{name}'[/yellow]")
        console.print("[dim]Use 'hcli ida source list' to see configured sources.[/dim]")
        return

    del sources[name]
    config_store.set_object("idb.sources", sources)

    console.print(f"[green]Removed source: '{name}'[/green]")
