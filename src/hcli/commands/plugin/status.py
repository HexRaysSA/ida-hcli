"""Plugin status command."""

from __future__ import annotations

import logging

import rich.table
import rich_click as click

from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.ida.plugin.install import get_installed_plugins, is_plugin_enabled

logger = logging.getLogger(__name__)


@click.command()
@click.pass_context
@async_command
async def get_plugin_status(ctx) -> None:
    try:
        table = rich.table.Table()
        table.add_column("name", style="blue")
        table.add_column("version", style="yellow")
        table.add_column("status")

        for name, version in get_installed_plugins():
            if is_plugin_enabled(name):
                table.add_row(name, version, "")
            else:
                table.add_row(f"[grey69]{name}[/grey69]", f"[grey69]{version}[/grey69]", "disabled")

        if table.row_count:
            console.print(table)
        else:
            console.print("[grey69]No plugins found[/grey69]")

    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
