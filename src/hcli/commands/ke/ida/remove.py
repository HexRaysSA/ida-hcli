from __future__ import annotations

import rich_click as click
from rich.console import Console

from hcli.lib.config import config_store

console = Console()


@click.command()
@click.argument("name", type=str)
def remove(name: str) -> None:
    """Remove an IDA Pro instance.

    NAME: Name of the IDA instance to remove
    """
    # Get existing instances
    instances: dict[str, str] = config_store.get_object("ke.ida.instances", {}) or {}

    if name not in instances:
        console.print(f"[red]IDA instance '{name}' not found[/red]")
        # Show available instances
        if instances:
            console.print("[yellow]Available instances:[/yellow]")
            for instance_name in instances.keys():
                console.print(f"  - {instance_name}")
        else:
            console.print("[yellow]No IDA instances registered. Use 'hcli ke ida add' to add instances.[/yellow]")
        raise click.Abort()

    # Check if this is the default instance
    default_instance = config_store.get_string("ke.ida.default", "")
    is_default = default_instance == name

    # Remove the instance
    del instances[name]
    config_store.set_object("ke.ida.instances", instances)

    # Clear default if removing the default instance
    if is_default:
        config_store.remove_string("ke.ida.default")
        console.print("[yellow]Removed default IDA instance setting[/yellow]")

    console.print(f"[green]Removed IDA instance '{name}'[/green]")
