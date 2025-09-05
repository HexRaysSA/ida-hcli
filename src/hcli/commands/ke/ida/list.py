from __future__ import annotations

from pathlib import Path

import rich_click as click
from rich.console import Console
from rich.table import Table

from hcli.lib.config import config_store
from hcli.lib.ida import is_ida_dir

console = Console()


@click.command()
def list_instances() -> None:
    """List all registered IDA Pro instances."""
    # Get existing instances and default
    instances: dict[str, str] = config_store.get_object("ke.ida.instances", {}) or {}
    default_instance = config_store.get_string("ke.ida.default", "")

    if not instances:
        console.print("[yellow]No IDA Pro instances registered.[/yellow]")
        console.print("[yellow]Use 'hcli ke ida add --auto' to discover and add IDA installations.[/yellow]")
        return

    # Create table
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name", style="cyan", width=20)
    table.add_column("Path", style="white")
    table.add_column("Status", style="green", width=15)
    table.add_column("Default", style="yellow", width=8)

    # Add rows
    for name, path_str in instances.items():
        path = Path(path_str)

        # Check if the path still exists and is valid
        if path.exists() and is_ida_dir(path):
            status = "Valid"
            status_style = "green"
        elif path.exists():
            status = "Invalid"
            status_style = "red"
        else:
            status = "Missing"
            status_style = "red"

        # Check if this is the default
        is_default = "✓" if name == default_instance else ""

        table.add_row(name, str(path), f"[{status_style}]{status}[/{status_style}]", is_default)

    console.print(table)

    # Show summary
    valid_count = sum(1 for path_str in instances.values() if Path(path_str).exists() and is_ida_dir(Path(path_str)))
    total_count = len(instances)

    console.print(f"\n[blue]Summary:[/blue] {valid_count}/{total_count} instances are valid")

    if default_instance:
        if default_instance in instances:
            console.print(f"[blue]Default instance:[/blue] {default_instance}")
        else:
            console.print(f"[red]Default instance '{default_instance}' no longer exists![/red]")
    else:
        console.print("[yellow]No default instance set. Use 'hcli ke ida switch' to set one.[/yellow]")
