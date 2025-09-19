"""Plugin status command."""

from __future__ import annotations

import logging

import rich.table
import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida import find_current_ida_platform, find_current_ida_version
from hcli.lib.ida.plugin import parse_plugin_version
from hcli.lib.ida.plugin.install import (
    get_installed_legacy_plugins,
    get_installed_minimal_plugins,
    get_installed_plugins,
    get_plugins_directory,
)
from hcli.lib.ida.plugin.repo import BasePluginRepo

logger = logging.getLogger(__name__)


@click.command()
@click.pass_context
def get_plugin_status(ctx) -> None:
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
                if parse_plugin_version(location.metadata.plugin.version) > parse_plugin_version(version):
                    status = f"upgradable to [yellow]{location.metadata.plugin.version}[/yellow]"
            except (ValueError, KeyError):
                status = "[yellow]not found in repository[/yellow]"

            table.add_row(name, version, status)

        has_incompatible_plugins = False
        plugin_directory = get_plugins_directory()
        for path, metadata in get_installed_minimal_plugins():
            plugin_path = path.parent.relative_to(plugin_directory)
            table.add_row(
                f"[grey69](incompatible)[/grey69] [blue]{metadata.plugin.name}[/blue]",
                metadata.plugin.version or "",
                f"[grey69]found at: $IDAPLUGINS/[/grey69]{plugin_path}/",
            )
            has_incompatible_plugins = True

        has_legacy_plugins = False
        for path in get_installed_legacy_plugins():
            plugin_path = path.parent.relative_to(plugin_directory)
            table.add_row(
                f"[grey69](legacy)[/grey69] [blue]{path.name}[/blue]",
                "",
                f"[grey69]found at: $IDAPLUGINS/[/grey69]{path.name}",
            )
            has_legacy_plugins = True

        if table.row_count:
            console.print(table)
        else:
            console.print("[grey69]No plugins found[/grey69]")

        if has_incompatible_plugins:
            console.print()
            console.print("[yellow]Incompatible plugins[/yellow] don't work with this version of hcli.")
            console.print("They might be broken or outdated. Try using `hcli plugin lint /path/to/plugin`.")

        if has_legacy_plugins:
            # TODO: suggest plugins in the repo, by maintaining a translation list from filename to package name
            console.print()
            console.print("[yellow]Legacy plugins[/yellow] are old, single-file plugins.")
            console.print("They aren't managed by hcli. Try finding an updated version in the plugin repository.")

    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
