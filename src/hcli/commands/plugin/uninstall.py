"""Plugin uninstall command."""

from __future__ import annotations

import logging

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida.plugin.exceptions import PluginNotInstalledError
from hcli.lib.ida.plugin.install import uninstall_plugin as uninstall_plugin_impl

logger = logging.getLogger(__name__)


@click.command()
@click.argument("plugin")
def uninstall_plugin(plugin: str) -> None:
    try:
        uninstall_plugin_impl(plugin)
    except PluginNotInstalledError as e:
        console.print(f"[red]{e}[/red]")
        raise click.Abort()
    except Exception as e:
        logger.debug("failed to uninstall: %s", e, exc_info=True)
        console.print(f"[red]uninstall failed: {e}[/red]")
        raise click.Abort()

    console.print(f"[green]Uninstalled[/green] plugin: [blue]{plugin}[/blue]")
