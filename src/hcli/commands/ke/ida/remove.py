from __future__ import annotations

import rich_click as click
from rich.console import Console

from hcli.lib.config import config_store

console = Console()


@click.command()
@click.option("--all", is_flag=True, help="Remove all registered IDA instances")
@click.argument("name", type=str, required=False)
def remove(all: bool, name: str | None) -> None:
    """Remove an IDA Pro instance.

    NAME: Name of the IDA instance to remove (required unless using --all)
    """
    # Validate arguments
    if all and name:
        console.print("[red]Cannot specify both --all and instance name[/red]")
        raise click.Abort()

    if not all and not name:
        console.print("[red]Must specify either --all or instance name[/red]")
        raise click.Abort()

    # Get existing instances
    instances: dict[str, str] = config_store.get_object("ke.ida.instances", {}) or {}

    if not instances:
        console.print("[yellow]No IDA instances registered.[/yellow]")
        return

    if all:
        # Remove all instances
        _remove_all_instances(instances)
    else:
        # Remove single instance (name is guaranteed to be not None due to validation above)
        assert name is not None
        _remove_single_instance(name, instances)


def _remove_all_instances(instances: dict[str, str]) -> None:
    """Remove all IDA instances."""
    instance_count = len(instances)
    instance_names = list(instances.keys())

    # Clear all instances
    config_store.set_object("ke.ida.instances", {})

    # Clear default setting
    config_store.remove_string("ke.ida.default")

    console.print(f"[green]Removed {instance_count} IDA instance(s):[/green]")
    for instance_name in instance_names:
        console.print(f"  - {instance_name}")
    console.print("[yellow]Cleared default IDA instance setting[/yellow]")


def _remove_single_instance(name: str, instances: dict[str, str]) -> None:
    """Remove a single IDA instance."""
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

    # Handle default instance removal
    if is_default:
        if instances:  # If there are remaining instances
            # Select the last alphabetical instance as the new default
            sorted_instance_names = sorted(instances.keys())
            new_default = sorted_instance_names[-1]
            config_store.set_string("ke.ida.default", new_default)
            console.print(f"[green]Set '{new_default}' as new default IDA instance[/green]")
        else:
            # No instances left, clear default
            config_store.remove_string("ke.ida.default")
            console.print("[yellow]No IDA instances remaining, cleared default setting[/yellow]")

    console.print(f"[green]Removed IDA instance '{name}'[/green]")
