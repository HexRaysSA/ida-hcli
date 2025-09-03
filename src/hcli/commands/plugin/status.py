"""Plugin status command."""

from __future__ import annotations

import logging

import rich.table
import rich_click as click

from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.ida import find_current_ida_platform, find_current_ida_version
from hcli.lib.ida.plugin import parse_plugin_version
from hcli.lib.ida.plugin.install import get_installed_plugins
from hcli.lib.ida.plugin.repo import BasePluginRepo

logger = logging.getLogger(__name__)


@click.command()
@click.pass_context
@async_command
async def get_plugin_status(ctx) -> None:
    plugin_repo: BasePluginRepo = ctx.obj["plugin_repo"]
    try:
        current_platform = find_current_ida_platform()
        current_ida_version = find_current_ida_version()

        table = rich.table.Table(show_header=False, box=None)
        table.add_column("name", style="blue")
        table.add_column("version", style="default")
        table.add_column("status")

        for name, version in get_installed_plugins():
            status = ""
            try:
                location = plugin_repo.find_compatible_plugin_from_spec(name, current_platform, current_ida_version)
                if parse_plugin_version(location.version) > parse_plugin_version(version):
                    status = f"upgradable to [yellow]{location.version}[/yellow]"
            except (ValueError, KeyError):
                status = "[grey69]not found in repository[/grey69]"

            table.add_row(name, version, status)

        if table.row_count:
            console.print(table)
        else:
            console.print("[grey69]No plugins found[/grey69]")

    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
