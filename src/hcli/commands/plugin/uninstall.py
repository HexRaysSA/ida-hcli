"""Plugin uninstall command."""

from __future__ import annotations

import questionary
import rich_click as click

from hcli.commands.common import safe_ask_async
from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.constants import cli


@click.command()
@click.argument("plugin", required=False)
@async_command
async def uninstall_plugin(plugin: str) -> None:
    if not plugin:
        query = await safe_ask_async(questionary.text("Enter search query:", style=cli.SELECT_STYLE))

    if not query.strip():
        console.print("[red]Plugin name cannot be empty[/red]")
        return

    try:
        raise NotImplementedError("Plugin uninstall")
    except Exception as e:
        console.print(f"[red]uninstall failed: {e}[/red]")
