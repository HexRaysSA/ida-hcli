"""Plugin search command."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import rich.table
import rich_click as click
import semantic_version

from hcli.lib.console import console
from hcli.lib.ida import (
    FailedToDetectIDAVersion,
    MissingCurrentInstallationDirectory,
    explain_failed_to_detect_ida_version,
    explain_missing_current_installation_directory,
    find_current_ida_platform,
    find_current_ida_version,
)
from hcli.lib.ida.plugin import (
    ALL_IDA_VERSIONS,
    ALL_PLATFORMS,
    IdaVersion,
    Platform,
    parse_ida_version,
    parse_plugin_version,
)
from hcli.lib.ida.plugin.exceptions import AmbiguousPluginReferenceError
from hcli.lib.ida.plugin.install import InstalledPluginRecord, find_installed_plugin_in, get_installed_plugin_records
from hcli.lib.ida.plugin.reference import (
    PluginReference,
    format_qualified_plugin_reference,
    parse_plugin_reference,
)
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


def find_installed_matching(
    plugin: Plugin,
    installed_records: list[InstalledPluginRecord],
) -> InstalledPluginRecord | None:
    """Look up the installed record for this *specific* repository plugin.

    Matches on both bare name and normalized host so a same-name plugin from a
    different repository does not register as installed.
    """
    return find_installed_plugin_in(installed_records, plugin.name, host=plugin.host)


def render_ambiguity_error(err: AmbiguousPluginReferenceError) -> None:
    """Render the user-facing message for an ambiguous bare-name query."""
    console.print(f"[red]Error[/red]: plugin name '{err.name}' is ambiguous")
    console.print("Choose one of:")
    for ref in err.candidate_refs:
        console.print(f"  {format_qualified_plugin_reference(ref)}")


def output_plugin_metadata(metadata) -> None:
    metadata_dict = metadata.plugin.model_dump()
    del metadata_dict["platforms"]
    metadata_dict["idaVersions"] = render_ida_versions(metadata_dict["idaVersions"])

    for key, value in sorted(metadata_dict.items()):
        console.print(f"{key}: {value}")
    console.print()


def output_plugin_versions_table(
    plugin: Plugin,
    versions: Sequence[str],
    current_version: str,
    current_platform: str,
    title: str,
    installed_records: list[InstalledPluginRecord],
) -> None:
    table = rich.table.Table(show_header=False, box=None)
    table.add_column("version", style="default")
    table.add_column("status")

    installed_record = find_installed_matching(plugin, installed_records)
    existing_version = None
    if installed_record is not None:
        existing_version = parse_plugin_version(installed_record.version)

    for version in versions:
        locations = plugin.versions[version]
        metadata = locations[0].metadata
        is_compatible = is_compatible_plugin_version(plugin, version, locations, current_platform, current_version)

        status = ""
        if installed_record is not None and existing_version is not None:
            if parse_plugin_version(metadata.plugin.version) == existing_version:
                status = "[green]currently installed[/green]"

            if parse_plugin_version(metadata.plugin.version) > existing_version and is_compatible:
                status = f"[yellow]upgradable[/yellow] from {existing_version}"

        elif not is_compatible:
            status = "[grey69]incompatible[/grey69]"

        table.add_row(version, status)

    console.print(title)
    console.print(table)


def get_matching_versions(plugin: Plugin, version_spec: str) -> list[str]:
    wanted_spec = semantic_version.SimpleSpec(version_spec)
    return [
        version
        for version, _ in sorted(plugin.versions.items(), key=lambda p: parse_plugin_version(p[0]), reverse=True)
        if parse_plugin_version(version) in wanted_spec
    ]


def handle_plugin_name_query(
    plugins: list[Plugin],
    ref: PluginReference,
    current_version: str,
    current_platform: str,
    installed_records: list[InstalledPluginRecord],
):
    plugin = get_plugin_by_name(plugins, ref.name, host=ref.host)
    output_plugin_metadata(get_latest_plugin_metadata(plugin))
    output_plugin_versions_table(
        plugin,
        [
            version
            for version, _ in sorted(plugin.versions.items(), key=lambda p: parse_plugin_version(p[0]), reverse=True)
        ],
        current_version,
        current_platform,
        "available versions:",
        installed_records,
    )


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


def handle_plugin_exact_version_query(plugin: Plugin, version: str):
    if version not in plugin.versions:
        raise KeyError(f"version {version} not found for plugin {plugin.name}")

    locations = plugin.versions[version]
    metadata = locations[0].metadata
    output_plugin_metadata(metadata)

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


def handle_plugin_version_range_query(
    plugin: Plugin,
    ref: PluginReference,
    current_version: str,
    current_platform: str,
    installed_records: list[InstalledPluginRecord],
):
    matching_versions = get_matching_versions(plugin, ref.version_spec)
    if not matching_versions:
        raise KeyError(f"no versions matching {ref.version_spec!r} found for plugin {plugin.name!r}")

    output_plugin_metadata(plugin.versions[matching_versions[0]][0].metadata)
    output_plugin_versions_table(
        plugin, matching_versions, current_version, current_platform, "matching versions:", installed_records
    )


def handle_plugin_spec_query(
    plugins: list[Plugin],
    ref: PluginReference,
    current_version: str,
    current_platform: str,
    installed_records: list[InstalledPluginRecord],
):
    plugin = get_plugin_by_name(plugins, ref.name, host=ref.host)

    if ref.version_spec.startswith("=="):
        version = ref.version_spec[2:]
        if not version:
            raise ValueError(f"invalid plugin version: {ref.version_spec!r}")
        handle_plugin_exact_version_query(plugin, version)
        return

    handle_plugin_version_range_query(plugin, ref, current_version, current_platform, installed_records)


def handle_keyword_query(
    plugins: list[Plugin],
    query: str,
    current_version: str,
    current_platform: str,
    installed_records: list[InstalledPluginRecord],
):
    table = rich.table.Table(show_header=False, box=None)
    table.add_column("name", style="blue")
    table.add_column("version", style="default")
    table.add_column("status")
    table.add_column("repo", style="grey69")
    has_matches = False

    for plugin in sorted(plugins, key=lambda p: p.name.lower()):
        if not does_plugin_match_query(query or "", plugin):
            continue

        has_matches = True
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

            installed_record = find_installed_matching(plugin, installed_records)
            is_upgradable = False
            existing_version: str | None = None
            if installed_record is not None:
                existing_version = installed_record.version
                if parse_plugin_version(latest_compatible_metadata.plugin.version) > parse_plugin_version(
                    existing_version
                ):
                    is_upgradable = True

            status = ""
            if is_upgradable:
                status = f"[yellow]upgradable[/yellow] from {existing_version}"
            elif installed_record is not None:
                status = "installed"

            table.add_row(
                f"[blue]{latest_metadata.plugin.name}[/blue]",
                latest_metadata.plugin.version,
                status,
                latest_metadata.plugin.urls.repository,
            )

    if has_matches:
        console.print(table)
    else:
        console.print("[grey69]No plugins found[/grey69]")


def _has_exact_name_match(plugins: list[Plugin], name: str) -> bool:
    wanted = name.lower()
    return any(p.name.lower() == wanted for p in plugins)


@click.command()
@click.argument("query", required=False)
@click.pass_context
def search_plugins(ctx, query: str | None = None) -> None:
    """Search for plugins by name, keyword, category, or author."""
    try:
        current_platform = find_current_ida_platform()
        current_version = find_current_ida_version()

        console.print(f"[grey69]current platform:[/grey69] {current_platform}")
        console.print(f"[grey69]current version:[/grey69] {current_version}")
        console.print()

        plugin_repo: BasePluginRepo = ctx.obj["plugin_repo"]
        plugins: list[Plugin] = plugin_repo.get_plugins()
        installed_records = get_installed_plugin_records()

        if not query:
            handle_keyword_query(plugins, "", current_version, current_platform, installed_records)
            return

        # Try to parse the query as a qualified reference. If parsing fails
        # (malformed version spec, etc.) fall back to keyword search so the
        # substring path continues to work for unusual user input.
        try:
            ref = parse_plugin_reference(query)
        except ValueError:
            handle_keyword_query(plugins, query, current_version, current_platform, installed_records)
            return

        # A qualified query (with a host) is always an exact plugin query.
        # An unqualified query is only an exact plugin query if it exactly
        # matches a known bare name case-insensitively; otherwise fall back
        # to keyword/substring search.
        if ref.host is None and not _has_exact_name_match(plugins, ref.name):
            handle_keyword_query(plugins, query, current_version, current_platform, installed_records)
            return

        try:
            if ref.version_spec:
                handle_plugin_spec_query(plugins, ref, current_version, current_platform, installed_records)
            else:
                handle_plugin_name_query(plugins, ref, current_version, current_platform, installed_records)
        except AmbiguousPluginReferenceError as e:
            # ``get_plugin_by_name`` does not know the user's version spec;
            # attach it here so candidate suggestions render ``name==1.2.3@repo``.
            if ref.version_spec and not e.version_spec:
                e = AmbiguousPluginReferenceError(e.name, e.candidates, ref.version_spec)
            render_ambiguity_error(e)
            raise click.Abort()

    except MissingCurrentInstallationDirectory:
        explain_missing_current_installation_directory(console)
        raise click.Abort()

    except FailedToDetectIDAVersion:
        explain_failed_to_detect_ida_version(console)
        raise click.Abort()

    except click.Abort:
        raise

    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
