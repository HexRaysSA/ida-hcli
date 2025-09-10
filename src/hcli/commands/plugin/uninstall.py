"""Plugin uninstall command."""

from __future__ import annotations

import logging

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida.plugin.install import (
    can_uninstall_plugin,
    is_plugin_installed,
)
from hcli.lib.ida.plugin.install import (
    uninstall_plugin as uninstall_plugin_impl,
)

logger = logging.getLogger(__name__)


@click.command()
@click.argument("plugin")
def uninstall_plugin(plugin: str) -> None:
    if not is_plugin_installed(plugin):
        console.print(f"[red]Plugin is not installed: {plugin}[/red]")
        raise click.Abort()

    if not can_uninstall_plugin(plugin):
        console.print(f"[red]Plugin cannot be uninstalled: {plugin}[/red]")
        raise click.Abort()

    try:
        uninstall_plugin_impl(plugin)
    except Exception as e:
        logger.error("failed to uninstall: %s", e, exc_info=True)
        console.print(f"[red]uninstall failed: {e}[/red]")
        raise click.Abort()

    console.print(f"[green]Uninstalled[/green] plugin: [blue]{plugin}[/blue]")
