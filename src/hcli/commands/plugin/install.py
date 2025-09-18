"""Plugin install command."""

from __future__ import annotations

import logging
from pathlib import Path

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida import find_current_ida_platform, find_current_ida_version
from hcli.lib.ida.plugin import (
    get_metadata_from_plugin_archive,
    get_metadatas_with_paths_from_plugin_archive,
    split_plugin_version_spec,
)
from hcli.lib.ida.plugin.install import install_plugin_archive
from hcli.lib.ida.plugin.repo import BasePluginRepo, fetch_plugin_archive

logger = logging.getLogger(__name__)


@click.command()
@click.pass_context
@click.argument("plugin")
def install_plugin(ctx, plugin: str) -> None:
    plugin_spec = plugin
    try:
        current_ida_platform = find_current_ida_platform()
        current_ida_version = find_current_ida_version()

        if Path(plugin_spec).exists() and plugin_spec.endswith(".zip"):
            logger.info("installing from the local file system")
            buf = Path(plugin_spec).read_bytes()
            items = list(get_metadatas_with_paths_from_plugin_archive(buf))
            if len(items) != 1:
                raise ValueError("plugin archive must contain a single plugin for local file system installation")
            plugin_name = items[0][1].plugin.name

        elif plugin_spec.startswith("file://"):
            logger.info("installing from the local file system")
            # fetch from file system
            buf = fetch_plugin_archive(plugin_spec)
            items = list(get_metadatas_with_paths_from_plugin_archive(buf))
            if len(items) != 1:
                raise ValueError("plugin archive must contain a single plugin for local file system installation")
            plugin_name = items[0][1].plugin.name

        elif plugin_spec.startswith("https://"):
            logger.info("installing from HTTP URL")
            buf = fetch_plugin_archive(plugin_spec)
            items = list(get_metadatas_with_paths_from_plugin_archive(buf))
            if len(items) != 1:
                raise ValueError("plugin archive must contain a single plugin for HTTP URL installation")
            plugin_name = items[0][1].plugin.name

        else:
            logger.info("finding plugin in repository")
            plugin_name, _ = split_plugin_version_spec(plugin_spec)
            logger.debug("plugin name: %s", plugin_name)

            plugin_repo: BasePluginRepo = ctx.obj["plugin_repo"]
            buf = plugin_repo.fetch_compatible_plugin_from_spec(plugin_spec, current_ida_platform, current_ida_version)

        install_plugin_archive(buf, plugin_name)

        metadata = get_metadata_from_plugin_archive(buf, plugin_name)

        console.print(f"[green]Installed[/green] plugin: [blue]{plugin_name}[/blue]=={metadata.plugin.version}")
    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
