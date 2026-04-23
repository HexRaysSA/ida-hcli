from __future__ import annotations

import rich_click as click
from rich.table import Table

from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console

from .common import fetch_tool_assets


@auth_command()
@click.argument("pattern", required=False, default=None)
@click.option("--all-versions", is_flag=True, help="Show all versions (default: latest only)")
@async_command
async def search_tools(pattern: str | None, all_versions: bool) -> None:
    """Search for available IDA tools."""
    assets = await fetch_tool_assets()

    if not assets:
        console.print("[yellow]No tools found.[/yellow]")
        return

    # Filter by pattern if provided
    if pattern:
        pattern_lower = pattern.lower()
        assets = [(k, name, ver) for k, name, ver in assets if pattern_lower in name.lower()]

    if not assets:
        console.print(f"[yellow]No tools matching '{pattern}'.[/yellow]")
        return

    # Group by tool name
    by_name: dict[str, list[tuple[str, str, str]]] = {}
    for asset_key, tool_name, version in assets:
        by_name.setdefault(tool_name, []).append((asset_key, tool_name, version))

    # Sort versions descending within each tool
    for entries in by_name.values():
        entries.sort(key=lambda x: x[2], reverse=True)

    table = Table(title="Available Tools")
    table.add_column("Name", style="cyan")
    table.add_column("Version", style="green")
    table.add_column("Install Command", style="blue")

    for tool_name in sorted(by_name):
        entries = by_name[tool_name]
        if all_versions:
            for asset_key, name, version in entries:
                table.add_row(name, version, f"hcli ida tool install {name}:{version}")
        else:
            # Latest only
            asset_key, name, version = entries[0]
            table.add_row(name, version, f"hcli ida tool install {name}:{version}")

    console.print(table)
