"""Plugin list command."""

from __future__ import annotations

import rich_click as click

from hcli.lib.commands import async_command
from hcli.lib.console import console


@click.command()
@async_command
async def list_plugins() -> None:
    try:
        raise NotImplementedError("Plugin list")
    except Exception as e:
        console.print(f"[red]list failed: {e}[/red]")
