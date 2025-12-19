from __future__ import annotations

import rich_click as click
from rich.console import Console

from hcli.lib.config import config_store

console = Console()


@click.command()
@click.argument("path")
def remove(path: str) -> None:
    """Remove a directory from IDB search paths.

    PATH: Path to remove (as shown in 'hcli idb search-path list').
    """
    paths: list[str] = config_store.get_object("idb.search-paths", []) or []

    if path not in paths:
        console.print(f"[yellow]Path not found in search paths: {path}[/yellow]")
        console.print("[dim]Use 'hcli idb search-path list' to see configured paths.[/dim]")
        return

    paths.remove(path)
    config_store.set_object("idb.search-paths", paths)

    console.print(f"[green]Removed search path: {path}[/green]")
