from __future__ import annotations

from pathlib import Path

import rich_click as click
from rich.console import Console

from hcli.lib.config import config_store

console = Console()


@click.command()
@click.argument("name", type=str)
@click.argument("path", type=click.Path(exists=True, path_type=Path))
def add(name: str, path: Path) -> None:
    """Add a knowledge source.

    NAME: Logical name for the source
    PATH: Filesystem path to the source
    """
    # Get existing sources or initialize empty dict
    sources: dict[str, str] = config_store.get_object("ke.sources", {}) or {}

    if name in sources:
        console.print(f"[yellow]Source '{name}' already exists. Use remove first to replace it.[/yellow]")
        raise click.Abort()

    # Store the absolute path as string
    sources[name] = str(path.absolute())

    # Save back to config
    config_store.set_object("ke.sources", sources)

    console.print(f"[green]Added source '{name}' pointing to '{path.absolute()}'[/green]")
