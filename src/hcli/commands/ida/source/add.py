from __future__ import annotations

import re
from pathlib import Path

import rich_click as click
from rich.console import Console

from hcli.lib.config import config_store

console = Console()

RESERVED_NAMES = {"localhost"}
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@click.command()
@click.argument("name")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--force", is_flag=True, help="Overwrite if name already exists")
def add(name: str, path: Path, force: bool) -> None:
    """Add a named source for IDB file lookup.

    NAME: Source name (lowercase alphanumeric + hyphens), used in ida:// URLs.
    PATH: Directory to search for IDB files.
    """
    # Validate name
    if name in RESERVED_NAMES:
        console.print(f"[red]Reserved name: '{name}'[/red]")
        raise click.Abort()

    if not NAME_PATTERN.match(name):
        console.print(
            f"[red]Invalid name: '{name}' (must be lowercase alphanumeric + hyphens, cannot start with hyphen)[/red]"
        )
        raise click.Abort()

    path = path.expanduser().resolve()

    if not path.is_dir():
        console.print(f"[red]Path is not a directory: {path}[/red]")
        raise click.Abort()

    sources: dict[str, str] = config_store.get_object("idb.sources", {}) or {}

    if name in sources and not force:
        console.print(f"[yellow]Source '{name}' already exists: {sources[name]}[/yellow]")
        console.print("[dim]Use --force to overwrite[/dim]")
        return

    sources[name] = str(path)
    config_store.set_object("idb.sources", sources)

    console.print(f"[green]Added source '{name}': {path}[/green]")
