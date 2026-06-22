from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import rich_click as click
from packaging.version import Version
from rich.console import Console
from rich.table import Table
from rich.text import Text

from hcli.env import ENV
from hcli.lib.config import config_store
from hcli.lib.ida import is_ida_dir, parse_instance_version

console = Console()


class InstanceRow(TypedDict):
    name: str
    path: Path
    status: str
    status_style: str
    is_default: bool
    version: Version | None


def _sort_instance_rows(instance_rows: list[InstanceRow]) -> None:
    """Sort rows by version descending, then name ascending."""
    instance_rows.sort(key=lambda row: row["name"])
    instance_rows.sort(key=lambda row: row["version"] or Version("0"), reverse=True)


@click.command()
def list_instances() -> None:
    """List all registered IDA Pro instances."""
    # Get existing instances and default
    instances: dict[str, str] = config_store.get_object("ida.instances", {}) or {}
    default_instance = config_store.get_string("ida.default", "")

    if not instances:
        console.print("[yellow]No IDA Pro instances registered.[/yellow]")
        console.print(
            f"[yellow]Use '{ENV.HCLI_BINARY_NAME} ida add --auto' to discover and add IDA installations.[/yellow]"
        )
        return

    # Create table
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name", style="cyan", min_width=20)
    table.add_column("Path", style="white")
    table.add_column("Status", style="green", width=15)

    instance_rows: list[InstanceRow] = []
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

        instance_rows.append(
            {
                "name": name,
                "path": path,
                "status": status,
                "status_style": status_style,
                "is_default": name == default_instance,
                "version": parse_instance_version(name, path),
            }
        )

    _sort_instance_rows(instance_rows)

    for row in instance_rows:
        display_name = Text(str(row["name"]), style="cyan")
        if row["is_default"]:
            display_name.append(" (default)", style="grey69")
        table.add_row(display_name, str(row["path"]), f"[{row['status_style']}]{row['status']}[/{row['status_style']}]")

    console.print(table)

    # Show summary
    valid_count = sum(1 for row in instance_rows if row["status"] == "Valid")
    total_count = len(instance_rows)

    console.print(f"\n[blue]Summary:[/blue] {valid_count}/{total_count} instances are valid")

    if default_instance:
        if default_instance in instances:
            console.print(f"[blue]Default instance:[/blue] {default_instance}")
            valid_rows = [row for row in instance_rows if row["status"] == "Valid"]
            _sort_instance_rows(valid_rows)
            latest_valid = valid_rows[0] if valid_rows else None
            if latest_valid and latest_valid["name"] != default_instance:
                console.print(
                    "[yellow]Latest IDA version is not the default. "
                    f"Use 'hcli ida switch {latest_valid['name']}' to update it.[/yellow]"
                )
        else:
            console.print(f"[red]Default instance '{default_instance}' no longer exists![/red]")
    else:
        console.print(f"[yellow]No default instance set. Use '{ENV.HCLI_BINARY_NAME} ida switch' to set one.[/yellow]")
