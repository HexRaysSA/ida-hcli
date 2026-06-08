from __future__ import annotations

from pathlib import Path

import rich_click as click
from packaging.version import Version
from rich.console import Console
from rich.table import Table

from hcli.lib.config import config_store
from hcli.lib.ida import is_ida_dir, parse_version_from_dir_name

console = Console()


def _parse_instance_version(name: str, path: Path) -> Version | None:
    """Parse an IDA instance version for display ordering."""
    raw_version = parse_version_from_dir_name(path) or parse_version_from_dir_name(Path(name))
    if raw_version is None:
        return None
    return Version(raw_version)


@click.command()
def list_instances() -> None:
    """List all registered IDA Pro instances."""
    # Get existing instances and default
    instances: dict[str, str] = config_store.get_object("ida.instances", {}) or {}
    default_instance = config_store.get_string("ida.default", "")

    if not instances:
        console.print("[yellow]No IDA Pro instances registered.[/yellow]")
        console.print("[yellow]Use 'hcli ida add --auto' to discover and add IDA installations.[/yellow]")
        return

    # Create table
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name", style="cyan", min_width=20)
    table.add_column("Path", style="white")
    table.add_column("Status", style="green", width=15)

    instance_rows = []
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
                "version": _parse_instance_version(name, path),
            }
        )

    instance_rows.sort(key=lambda row: row["name"])
    instance_rows.sort(key=lambda row: row["version"] or Version("0"), reverse=True)
    instance_rows.sort(key=lambda row: not row["is_default"])

    for row in instance_rows:
        display_name = row["name"]
        if row["is_default"]:
            display_name += " (default)"
        table.add_row(display_name, str(row["path"]), f"[{row['status_style']}]{row['status']}[/{row['status_style']}]")

    console.print(table)

    # Show summary
    valid_count = sum(1 for row in instance_rows if row["status"] == "Valid")
    total_count = len(instance_rows)

    console.print(f"\n[blue]Summary:[/blue] {valid_count}/{total_count} instances are valid")

    if default_instance:
        if default_instance in instances:
            console.print(f"[blue]Default instance:[/blue] {default_instance}")
            latest_valid = max(
                (row for row in instance_rows if row["status"] == "Valid"),
                key=lambda row: row["version"] or Version("0"),
                default=None,
            )
            if latest_valid and latest_valid["name"] != default_instance:
                console.print(
                    "[yellow]Latest valid IDA installation is not the default. "
                    f"Use 'hcli ida switch {latest_valid['name']}' to update it.[/yellow]"
                )
        else:
            console.print(f"[red]Default instance '{default_instance}' no longer exists![/red]")
    else:
        console.print("[yellow]No default instance set. Use 'hcli ida switch' to set one.[/yellow]")
