"""Plugin search command."""

from __future__ import annotations

import logging

import rich_click as click

from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.ida import find_current_ida_platform, find_current_ida_version
from hcli.lib.ida.plugin import ALL_PLATFORMS, is_ida_version_compatible
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


@click.command()
@click.argument("query", required=False)
@click.pass_context
@async_command
async def search_plugins(ctx, query: str | None = None) -> None:
    try:
        current_platform = find_current_ida_platform()
        current_version = find_current_ida_version()

        console.print(f"[grey69]current platform:[/grey69] {current_platform}")
        console.print(f"[grey69]current version:[/grey69] {current_version}")
        console.print()

        plugin_repo: BasePluginRepo = ctx.obj["plugin_repo"]

        plugins: list[Plugin] = plugin_repo.get_plugins()
        incompatible_plugins: list[str] = []

        for plugin in sorted(plugins, key=lambda p: p.name):
            if query and query.lower() not in plugin.name.lower():
                continue

            if not is_compatible_plugin(plugin, current_platform, current_version):
                incompatible_plugins.append(plugin.name)
                continue

            console.print(f"[blue]{plugin.name}[/blue]")

            for version, locations in plugin.versions.items():
                if not is_compatible_plugin_version(plugin, version, locations, current_platform, current_version):
                    continue

                console.print(f"  {version}:")
                for location in locations:
                    if not is_compatible_plugin_version_location(
                        plugin, version, location, current_platform, current_version
                    ):
                        continue

                    ida_versions_str = location.ida_versions if location.ida_versions != ">=0" else "all"
                    locations_str = ", ".join(location.platforms) if location.platforms != ALL_PLATFORMS else "all"
                    console.print(
                        f"    IDA: [yellow]{ida_versions_str}[/yellow], platforms: [yellow]{locations_str}[/yellow]"
                    )
                    console.print(f"    {location.url}")
                    console.print("")

        if not plugins:
            console.print("[grey69]No plugins found[/grey69]")

        if incompatible_plugins:
            console.print("[grey69]Incompatible plugins:[/grey69]")
            for incompatible_plugin in sorted(set(incompatible_plugins)):
                console.print(f"- [blue]{incompatible_plugin}[/blue]")

    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
