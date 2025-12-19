from __future__ import annotations

from pathlib import Path

import rich_click as click
from rich.console import Console

from hcli.lib.config import config_store

console = Console()


@click.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
def add(path: Path) -> None:
    """Add a directory to IDB search paths.

    PATH: Directory to search for IDB files when auto-launching IDA.
    """
    path = path.expanduser().resolve()

    if not path.is_dir():
        console.print(f"[red]Path is not a directory: {path}[/red]")
        raise click.Abort()

    paths: list[str] = config_store.get_object("idb.search-paths", []) or []

    # Check for duplicates
    path_str = str(path)
    if path_str in paths:
        console.print(f"[yellow]Path already configured: {path}[/yellow]")
        return

    paths.append(path_str)
    config_store.set_object("idb.search-paths", paths)

    console.print(f"[green]Added search path: {path}[/green]")
