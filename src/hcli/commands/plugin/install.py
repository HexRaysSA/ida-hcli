"""Plugin install command."""

from __future__ import annotations

import questionary
import rich_click as click

from hcli.commands.common import safe_ask_async
from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.constants import cli


@click.command()
@click.argument("plugin")
@async_command
async def install_plugin(plugin: str) -> None:

    try:
        raise NotImplementedError("Plugin install")
    except Exception as e:
        console.print(f"[red]install failed: {e}[/red]")
