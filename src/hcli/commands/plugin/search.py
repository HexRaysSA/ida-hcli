"""Plugin search command."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import rich.table
import rich_click as click
import yaml

from hcli.lib.console import console
from hcli.lib.ida import find_current_ida_platform, find_current_ida_version
from hcli.lib.ida.plugin import (
    ALL_IDA_VERSIONS,
    ALL_PLATFORMS,
    IdaVersion,
    Platform,
    parse_ida_version,
    parse_plugin_version,
    split_plugin_version_spec,
)
from hcli.lib.ida.plugin.install import get_metadata_from_plugin_directory, get_plugin_directory, is_plugin_installed
from hcli.lib.ida.plugin.repo import (
    BasePluginRepo,
    Plugin,
    get_latest_compatible_plugin_metadata,
    get_latest_plugin_metadata,
    get_plugin_by_name,
    is_compatible_plugin,
    is_compatible_plugin_version,
)

logger = logging.getLogger(__name__)


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


def is_plugin_name_query(plugins: list[Plugin], query: str):
    """like 'plugin1' exact matches a known plugin"""
    if not query:
        return False

    query = query.lower()

    try:
        _ = get_plugin_by_name(plugins, query)
        return True
    except KeyError:
        return False


def is_plugin_spec_query(plugins: list[Plugin], query: str):
    """like 'plugin1==1.0.0' exact matches a known plugin name, with version"""
    try:
        plugin_name, _ = split_plugin_version_spec(query)
    except ValueError:
        return False

    if plugin_name != query and is_plugin_name_query(plugins, plugin_name):
        return True

    return False


def handle_plugin_name_query(plugins: list[Plugin], query: str, current_version: str, current_platform: str):
    plugin = get_plugin_by_name(plugins, query)
    latest_metadata = get_latest_plugin_metadata(plugin)

    metadata_dict = latest_metadata.plugin.model_dump()
    del metadata_dict["platforms"]
    metadata_dict["idaVersions"] = render_ida_versions(metadata_dict["idaVersions"])

    yaml_str = yaml.dump(metadata_dict, default_flow_style=False, sort_keys=True)
    console.print(yaml_str)

    # i had hoped to use markdown/syntax, but it always has a background color, and we dont know light/dark theme state.

    table = rich.table.Table(show_header=False, box=None)
    table.add_column("version", style="default")
    table.add_column("status")

    is_installed = is_plugin_installed(plugin.name)
    existing_version = None
    if is_installed:
        existing_plugin_path = get_plugin_directory(plugin.name)
        existing_metadata = get_metadata_from_plugin_directory(existing_plugin_path)
        existing_version = parse_plugin_version(existing_metadata.plugin.version)

    for version, locations in sorted(plugin.versions.items(), key=lambda p: parse_plugin_version(p[0]), reverse=True):
        metadata = locations[0].metadata

        is_compatible = is_compatible_plugin_version(plugin, version, locations, current_platform, current_version)

        status = ""
        if is_installed:
            if is_installed and parse_plugin_version(metadata.plugin.version) == existing_version:
                status = "[green]currently installed[/green]"

            if is_installed and parse_plugin_version(metadata.plugin.version) > existing_version:
                if is_compatible:
                    status = f"[yellow]upgradable[/yellow] from {existing_version}"

        else:
            if not is_compatible:
                status = "[grey69]incompatible[/grey69]"

        table.add_row(
            version,
            status,
        )

    console.print("available versions:")
    console.print(table)


def render_ida_versions(versions: Sequence[IdaVersion]) -> str:
    if frozenset(versions) == ALL_IDA_VERSIONS:
        return "all"

    ordered_versions = sorted(versions, key=parse_ida_version)

    if len(ordered_versions) == 1:
        return ordered_versions[0]

    # assume there are no holes. we could make this more complete if required.
    return f"{ordered_versions[0]}-{ordered_versions[-1]}"


def render_platforms(platforms: Sequence[Platform]) -> str:
    if frozenset(platforms) == ALL_PLATFORMS:
        return "all"

    return ", ".join(sorted(platforms))


def handle_plugin_spec_query(plugins: list[Plugin], query: str, current_version: str, current_platform: str):
    name, version = split_plugin_version_spec(query)
    if not version:
        raise ValueError(f"invalid plugin version: {query}")

    plugin = get_plugin_by_name(plugins, name)

    locations = plugin.versions[version]
    metadata = locations[0].metadata

    metadata_dict = metadata.plugin.model_dump()
    del metadata_dict["platforms"]
    metadata_dict["idaVersions"] = render_ida_versions(metadata_dict["idaVersions"])

    yaml_str = yaml.dump(metadata_dict, default_flow_style=False, sort_keys=True)
    console.print(yaml_str)

    table = rich.table.Table(show_header=False, box=None)
    table.add_column("IDA version spec", style="default")
    table.add_column("IDA platforms", style="default")
    table.add_column("URL")

    for location in locations:
        table.add_row(
            "IDA: " + render_ida_versions(location.metadata.plugin.ida_versions),
            "platforms: " + render_platforms(location.metadata.plugin.platforms),
            "URL: " + location.url,
        )

    console.print("download locations:")
    console.print(table)


def handle_keyword_query(plugins: list[Plugin], query: str, current_version: str, current_platform: str):
    table = rich.table.Table(show_header=False, box=None)
    table.add_column("name", style="blue")
    table.add_column("version", style="default")
    table.add_column("status")
    table.add_column("repo", style="grey69")

    for plugin in sorted(plugins, key=lambda p: p.name.lower()):
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
        plugins: list[Plugin] = plugin_repo.get_plugins()

        if is_plugin_name_query(plugins, query or ""):
            handle_plugin_name_query(plugins, query or "", current_version, current_platform)

        elif is_plugin_spec_query(plugins, query or ""):
            handle_plugin_spec_query(plugins, query or "", current_version, current_platform)
        else:
            handle_keyword_query(plugins, query or "", current_version, current_platform)

    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
