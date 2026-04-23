from __future__ import annotations

from pathlib import Path

import rich_click as click

from hcli.lib.commands import async_command
from hcli.lib.config import config_store
from hcli.lib.console import console


@click.command()
@click.argument("name")
@click.option("--keep-binary", is_flag=True, help="Remove from config but keep the binary file")
@async_command
async def remove_tool(name: str, keep_binary: bool) -> None:
    """Remove an installed IDA-related utility tool.

    NAME: The name of the tool to remove (e.g., 'vault2git')
    """
    installed: dict = config_store.get_object("tools.installed", {}) or {}

    if name not in installed:
        console.print(f"[red]Tool '{name}' is not installed.[/red]")
        if installed:
            console.print("[yellow]Installed tools:[/yellow]")
            for tool_name in installed:
                console.print(f"  - {tool_name}")
        return

    tool_info = installed[name]
    tool_path = tool_info.get("path", "")

    # Delete binary unless --keep-binary
    if not keep_binary and tool_path:
        Path(tool_path).unlink(missing_ok=True)
        console.print(f"[green]Removed binary: {tool_path}[/green]")

    # Remove from config
    del installed[name]
    config_store.set_object("tools.installed", installed)

    console.print(f"[green]Successfully removed {name}[/green]")
