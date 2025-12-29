from __future__ import annotations

from pathlib import Path

import rich_click as click
from rich.console import Console

from hcli.lib.config import config_store

console = Console()


@click.command(name="list")
def list_paths() -> None:
    """List configured IDB search paths."""
    paths: list[str] = config_store.get_object("idb.search-paths", []) or []

    if not paths:
        console.print("[yellow]No IDB search paths configured.[/yellow]")
        console.print("[dim]Add paths with: hcli idb search-path add <path>[/dim]")
        return

    console.print(f"[green]IDB search paths ({len(paths)}):[/green]")
    for p in paths:
        path_obj = Path(p)
        if path_obj.exists():
            console.print(f"  {p}")
        else:
            console.print(f"  {p} [red](not found)[/red]")
