from __future__ import annotations

import questionary
import rich_click as click
from rich.console import Console

from hcli.lib.config import config_store

console = Console()


@click.command()
@click.argument("name", required=False)
def switch(name: str | None) -> None:
    """Switch the default IDA Pro instance.

    NAME: Name of the IDA instance to set as default (optional - will prompt if not provided)
    """
    # Get existing instances
    instances: dict[str, str] = config_store.get_object("ke.ida.instances", {}) or {}

    if not instances:
        console.print("[yellow]No IDA Pro instances registered.[/yellow]")
        console.print("[yellow]Use 'hcli ke ida add --auto' to discover and add IDA installations.[/yellow]")
        raise click.Abort()

    # If name is provided, validate and set it
    if name:
        if name not in instances:
            console.print(f"[red]IDA instance '{name}' not found[/red]")
            console.print("[yellow]Available instances:[/yellow]")
            for instance_name in instances.keys():
                console.print(f"  - {instance_name}")
            raise click.Abort()

        _set_default_instance(name)
        return

    # Interactive selection
    current_default = config_store.get_string("ke.ida.default", "")

    # Create choices with current default marked
    choices = []
    for instance_name, path in instances.items():
        display_name = f"{instance_name} ({path})"
        if instance_name == current_default:
            display_name += " [CURRENT DEFAULT]"
        choices.append(questionary.Choice(title=display_name, value=instance_name))

    try:
        selected = questionary.select(
            "Select default IDA Pro instance:",
            choices=choices,
            default=current_default if current_default in instances else None,
        ).ask()

        if not selected:
            console.print("[yellow]No instance selected[/yellow]")
            return

        _set_default_instance(selected)

    except KeyboardInterrupt:
        console.print("\n[yellow]Selection cancelled[/yellow]")
        raise click.Abort()


def _set_default_instance(name: str) -> None:
    """Set the default IDA instance."""
    config_store.set_string("ke.ida.default", name)
    console.print(f"[green]Set '{name}' as the default IDA Pro instance[/green]")
