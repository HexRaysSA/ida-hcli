from __future__ import annotations

import logging
from pathlib import Path

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida import find_current_ida_platform, find_current_ida_version
from hcli.lib.ida.plugin import get_metadata_from_plugin_archive, split_plugin_version_spec
from hcli.lib.ida.plugin.install import upgrade_plugin_archive
from hcli.lib.ida.plugin.repo import BasePluginRepo

logger = logging.getLogger(__name__)


@click.command()
@click.pass_context
@click.argument("plugin")
def upgrade_plugin(ctx, plugin: str) -> None:
    plugin_spec = plugin
    try:
        current_ida_platform = find_current_ida_platform()
        current_ida_version = find_current_ida_version()

        if Path(plugin_spec).exists() and plugin_spec.endswith(".zip"):
            raise ValueError("cannot upgrade using local file; uninstall/reinstall instead")

        if plugin_spec.startswith("file://"):
            raise ValueError("cannot upgrade using local file; uninstall/reinstall instead")

        if plugin_spec.startswith("https://"):
            raise ValueError("cannot upgrade using URL; uninstall/reinstall instead")

        logger.info("finding plugin in repository")
        plugin_name, _ = split_plugin_version_spec(plugin_spec)
        logger.debug("plugin name: %s", plugin_name)

        plugin_repo: BasePluginRepo = ctx.obj["plugin_repo"]
        buf = plugin_repo.fetch_compatible_plugin_from_spec(plugin_spec, current_ida_platform, current_ida_version)

        upgrade_plugin_archive(buf, plugin_name)

        metadata = get_metadata_from_plugin_archive(buf, plugin_name)

        console.print(f"[green]Installed[/green] plugin: [blue]{plugin_name}[/blue]=={metadata.plugin.version}")
    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
