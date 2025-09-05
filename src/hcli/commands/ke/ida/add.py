from __future__ import annotations

from pathlib import Path

import questionary
import rich_click as click
from rich.console import Console
from rich.table import Table

from hcli.lib.config import config_store
from hcli.lib.ida import add_instance_to_config, find_standard_installations, generate_instance_name, is_ida_dir

console = Console()


@click.command()
@click.option("--auto", is_flag=True, help="Automatically discover standard IDA installations")
@click.argument("name", required=False)
@click.argument("path", type=click.Path(exists=True, path_type=Path), required=False)
def add(auto: bool, name: str | None, path: Path | None) -> None:
    """Add an IDA Pro instance.

    Without --auto: Add a specific IDA instance by name and path.
    With --auto: Discover and interactively add standard IDA installations.

    NAME: Logical name for the IDA instance
    PATH: Base path to the IDA installation directory
    """
    if auto:
        # Auto-discovery mode
        _add_auto_discovered_instances()
    else:
        # Manual mode
        if not name or not path:
            console.print("[red]Both NAME and PATH are required when not using --auto[/red]")
            raise click.Abort()

        _add_manual_instance(name, path)


def _add_auto_discovered_instances() -> None:
    """Auto-discover and add IDA installations."""
    console.print("[blue]Discovering standard IDA installations...[/blue]")

    try:
        installations = find_standard_installations()
    except Exception as e:
        console.print(f"[red]Error discovering installations: {e}[/red]")
        raise click.Abort()

    if not installations:
        console.print("[yellow]No standard IDA installations found[/yellow]")
        return

    # Filter to valid IDA directories
    valid_installations = []
    for installation in installations:
        if is_ida_dir(installation):
            valid_installations.append(installation)

    if not valid_installations:
        console.print("[yellow]No valid IDA installations found[/yellow]")
        return

    console.print(f"[green]Found {len(valid_installations)} IDA installation(s)[/green]")

    # Get existing instances
    instances: dict[str, str] = config_store.get_object("ke.ida.instances", {}) or {}

    # Show table of found installations
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Index", style="dim", width=5)
    table.add_column("Installation Path", style="white")
    table.add_column("Status", style="green")

    for i, installation in enumerate(valid_installations, 1):
        status = "Already registered" if installation.name in [Path(p).name for p in instances.values()] else "New"
        table.add_row(str(i), str(installation), status)

    console.print(table)

    # Interactive selection
    choices = []
    for i, installation in enumerate(valid_installations):
        # Create a reasonable name from the installation path
        suggested_name = generate_instance_name(installation)
        status = " (already registered)" if suggested_name in instances else ""
        choices.append(
            {
                "name": f"{installation}{status}",
                "value": (suggested_name, installation),
                "checked": suggested_name not in instances,  # Pre-check new installations
            }
        )

    if not choices:
        console.print("[yellow]No new installations to add[/yellow]")
        return

    try:
        selected = questionary.checkbox(
            "Select IDA installations to register:",
            choices=choices,
            validate=lambda x: True if x else "Please select at least one installation",
        ).ask()

        if not selected:
            console.print("[yellow]No installations selected[/yellow]")
            return

        # Add selected installations
        added_count = 0
        added_instances = []
        for suggested_name, installation_path in selected:
            if add_instance_to_config(suggested_name, installation_path):
                added_count += 1
                added_instances.append((suggested_name, installation_path))

        console.print(f"[green]Added {added_count} IDA instance(s)[/green]")

        # Set default if no default exists and instances were added
        if added_count > 0:
            default_instance = config_store.get_string("ke.ida.default", "")
            if not default_instance:
                # Sort added instances alphabetically and pick the last one
                sorted_instances = sorted(added_instances, key=lambda x: x[0])
                last_instance_name = sorted_instances[-1][0]
                config_store.set_string("ke.ida.default", last_instance_name)
                console.print(f"[green]Set '{last_instance_name}' as default IDA instance[/green]")

    except KeyboardInterrupt:
        console.print("\n[yellow]Selection cancelled[/yellow]")
        raise click.Abort()


def _add_manual_instance(name: str, path: Path) -> None:
    """Add a specific IDA instance manually."""
    # Check if it's a valid IDA directory
    if not is_ida_dir(path):
        console.print(f"[red]Invalid IDA installation directory: {path}[/red]")
        console.print("[yellow]The directory should contain the IDA binary[/yellow]")
        raise click.Abort()

    success = add_instance_to_config(name, path)
    if success:
        console.print(f"[green]Added IDA instance '{name}' at '{path}'[/green]")
    else:
        console.print(f"[yellow]IDA instance '{name}' already exists. Use remove first to replace it.[/yellow]")
