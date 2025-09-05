from __future__ import annotations

import rich_click as click
from rich.console import Console

from hcli.lib.config import config_store

console = Console()


@click.command()
@click.argument("name", type=str)
def remove(name: str) -> None:
    """Remove a knowledge source.

    NAME: Name of the source to remove
    """
    # Get existing sources
    sources: dict[str, str] = config_store.get_object("ke.sources", {}) or {}

    if name not in sources:
        console.print(f"[red]Source '{name}' not found[/red]")
        raise click.Abort()

    # Remove the source
    del sources[name]

    # Save back to config
    config_store.set_object("ke.sources", sources)

    console.print(f"[green]Removed source '{name}'[/green]")
