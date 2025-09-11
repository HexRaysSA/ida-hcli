"""Plugin search command."""

from __future__ import annotations

import logging

import rich.table
import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida import find_current_ida_platform, find_current_ida_version
from hcli.lib.ida.plugin import (
    IDAMetadataDescriptor,
    is_ida_version_compatible,
    parse_plugin_version,
)
from hcli.lib.ida.plugin.install import get_metadata_from_plugin_directory, get_plugin_directory, is_plugin_installed
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin, PluginArchiveLocation

logger = logging.getLogger(__name__)


def is_compatible_plugin_version_location(
    plugin: Plugin, version: str, location: PluginArchiveLocation, current_platform: str, current_version: str
) -> bool:
    if not is_ida_version_compatible(current_version, location.ida_versions):
        return False

    if current_platform not in location.platforms:
        return False

    return True


def is_compatible_plugin_version(
    plugin: Plugin, version: str, locations: list[PluginArchiveLocation], current_platform: str, current_version: str
) -> bool:
    return any(
        is_compatible_plugin_version_location(plugin, version, location, current_platform, current_version)
        for location in locations
    )


def is_compatible_plugin(plugin: Plugin, current_platform: str, current_version: str) -> bool:
    return any(
        is_compatible_plugin_version(plugin, version, locations, current_platform, current_version)
        for version, locations in plugin.versions.items()
    )


def get_latest_plugin_metadata(plugin: Plugin) -> IDAMetadataDescriptor:
    max_version = max(plugin.versions.keys(), key=parse_plugin_version)
    max_locations = plugin.versions[max_version]
    return max_locations[0].metadata


def get_latest_compatible_plugin_metadata(
    plugin: Plugin, current_platform: str, current_version: str
) -> IDAMetadataDescriptor:
    for version, locations in sorted(plugin.versions.items(), key=lambda p: parse_plugin_version(p[0]), reverse=True):
        if is_compatible_plugin_version(plugin, version, locations, current_platform, current_version):
            return plugin.versions[version][0].metadata

    raise ValueError("no versions of plugin are compatible")


def does_plugin_match_query(query: str, plugin: Plugin) -> bool:
    if not query:
        return True

    query = query.lower()

    if query in plugin.name.lower():
        return True

    for locations in plugin.versions.values():
        for location in locations:
            md = location.metadata.plugin
            for category in md.categories:
                if query in category.lower():
                    return True

            for keyword in md.keywords:
                if query in keyword.lower():
                    return True

            if md.description and query in md.description.lower():
                return True

            for author in md.authors:
                if not author.name:
                    continue

                if query in author.name.lower():
                    return True

            for maintainer in md.maintainers:
                if not maintainer.name:
                    continue

                if query in maintainer.name.lower():
                    return True

    return False


@click.command()
@click.argument("query", required=False)
@click.pass_context
def search_plugins(ctx, query: str | None = None) -> None:
    try:
        current_platform = find_current_ida_platform()
        current_version = find_current_ida_version()

        console.print(f"[grey69]current platform:[/grey69] {current_platform}")
        console.print(f"[grey69]current version:[/grey69] {current_version}")
        console.print()

        plugin_repo: BasePluginRepo = ctx.obj["plugin_repo"]

        table = rich.table.Table(show_header=False, box=None)
        table.add_column("name", style="blue")
        table.add_column("version", style="default")
        table.add_column("status")
        table.add_column("repo", style="grey69")

        plugins: list[Plugin] = plugin_repo.get_plugins()
        for plugin in sorted(plugins, key=lambda p: p.name.lower()):

            # TODO: if query is plugin name exact match, show details of that plugin
            # TODO: if query is plugin name+version exact match, show details of that version
            
            if not does_plugin_match_query(query or "", plugin):
                continue

            latest_metadata = get_latest_plugin_metadata(plugin)

            if not is_compatible_plugin(plugin, current_platform, current_version):
                table.add_row(
                    f"[grey69]{latest_metadata.plugin.name} (incompatible)[/grey69]",
                    f"[grey69]{latest_metadata.plugin.version}[/grey69]",
                    "",
                    latest_metadata.plugin.urls.repository,
                )

            else:
                latest_compatible_metadata = get_latest_compatible_plugin_metadata(
                    plugin, current_platform, current_version
                )

                is_installed = is_plugin_installed(plugin.name)
                is_upgradable = False
                existing_version = None
                if is_installed:
                    existing_plugin_path = get_plugin_directory(plugin.name)
                    existing_metadata = get_metadata_from_plugin_directory(existing_plugin_path)
                    existing_version = existing_metadata.plugin.version
                    if parse_plugin_version(latest_compatible_metadata.plugin.version) > parse_plugin_version(
                        existing_version
                    ):
                        is_upgradable = True

                status = ""
                if is_upgradable:
                    status = f"[yellow]upgradable[/yellow] from {existing_version}"
                elif is_installed:
                    status = "installed"

                table.add_row(
                    f"[blue]{latest_metadata.plugin.name}[/blue]",
                    latest_metadata.plugin.version,
                    status,
                    latest_metadata.plugin.urls.repository,
                )

        console.print(table)

        if not plugins:
            console.print("[grey69]No plugins found[/grey69]")

    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
