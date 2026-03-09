from __future__ import annotations

from pathlib import Path

import rich_click as click
from rich.console import Console

from hcli.lib.config import config_store

console = Console()


@click.command(name="list")
def list_sources() -> None:
    """List configured sources."""
    sources: dict[str, str] = config_store.get_object("idb.sources", {}) or {}

    if not sources:
        console.print("[yellow]No sources configured.[/yellow]")
        console.print("[dim]Add sources with: hcli ida source add <name> <path>[/dim]")
        return

    console.print(f"[green]Sources ({len(sources)}):[/green]")
    for name, path_str in sources.items():
        path_obj = Path(path_str)
        if path_obj.exists():
            console.print(f"  {name} -> {path_str}")
        else:
            console.print(f"  {name} -> {path_str} [red](not found)[/red]")
