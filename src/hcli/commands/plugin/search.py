"""Plugin search command."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import rich.table
import rich_click as click
import semantic_version
from pydantic import BaseModel

from hcli.lib.console import console, print_json
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


class VersionEntry(BaseModel):
    version: str
    compatible: bool
    currently_installed: bool
    upgradable: bool


class DownloadLocationEntry(BaseModel):
    ida_versions: str
    platforms: str
    url: str


class PluginNameQueryResult(BaseModel):
    plugin: dict[str, Any]
    installed_version: str | None
    versions: list[VersionEntry]


class PluginExactVersionQueryResult(BaseModel):
    plugin: dict[str, Any]
    download_locations: list[DownloadLocationEntry]


class PluginVersionRangeQueryResult(BaseModel):
    plugin: dict[str, Any]
    installed_version: str | None
    versions: list[VersionEntry]


class KeywordMatchEntry(BaseModel):
    name: str
    version: str
    repository: str | None
    compatible: bool
    installed: bool
    installed_version: str | None
    upgradable: bool


class KeywordQueryResult(BaseModel):
    query: str | None
    results: list[KeywordMatchEntry]


class AmbiguityErrorResult(BaseModel):
    error: str
    name: str
    candidates: list[str]


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


def collect_ambiguity_error(err: AmbiguousPluginReferenceError) -> AmbiguityErrorResult:
    return AmbiguityErrorResult(
        error="ambiguous plugin reference",
        name=err.name,
        candidates=[format_qualified_plugin_reference(ref) for ref in err.candidate_refs],
    )


def render_ambiguity_error_text(err: AmbiguousPluginReferenceError) -> None:
    """Render the user-facing message for an ambiguous bare-name query."""
    console.print(f"[red]Error[/red]: plugin name '{err.name}' is ambiguous")
    console.print("Choose one of:")
    for ref in err.candidate_refs:
        console.print(f"  {format_qualified_plugin_reference(ref)}")


def collect_plugin_metadata(metadata) -> dict[str, Any]:
    metadata_dict = metadata.plugin.model_dump(mode="json")
    del metadata_dict["platforms"]
    metadata_dict["idaVersions"] = render_ida_versions(metadata_dict["idaVersions"])
    return metadata_dict


def render_plugin_metadata_text(plugin: dict[str, Any]) -> None:
    for key, value in sorted(plugin.items()):
        console.print(f"{key}: {value}")
    console.print()


def collect_version_entries(
    plugin: Plugin,
    versions: Sequence[str],
    current_version: str,
    current_platform: str,
    installed_records: list[InstalledPluginRecord],
) -> tuple[list[VersionEntry], str | None]:
    """Build per-version compatibility/install status entries for `plugin`.

    Returns (entries, installed_version), where installed_version is the
    version string currently installed, or None if this specific repository
    plugin (name+host) isn't installed.
    """
    installed_record = find_installed_matching(plugin, installed_records)
    existing_version = None
    if installed_record is not None:
        existing_version = parse_plugin_version(installed_record.version)

    entries: list[VersionEntry] = []
    for version in versions:
        locations = plugin.versions[version]
        metadata = locations[0].metadata
        is_compatible = is_compatible_plugin_version(plugin, version, locations, current_platform, current_version)

        currently_installed = False
        upgradable = False
        if installed_record is not None and existing_version is not None:
            if parse_plugin_version(metadata.plugin.version) == existing_version:
                currently_installed = True
            if parse_plugin_version(metadata.plugin.version) > existing_version and is_compatible:
                upgradable = True

        entries.append(
            VersionEntry(
                version=version,
                compatible=is_compatible,
                currently_installed=currently_installed,
                upgradable=upgradable,
            )
        )

    return entries, (installed_record.version if installed_record is not None else None)


def render_plugin_versions_text(entries: list[VersionEntry], installed_version: str | None, title: str) -> None:
    table = rich.table.Table(show_header=False, box=None)
    table.add_column("version", style="default")
    table.add_column("status")

    for entry in entries:
        status = ""
        # An installed version takes precedence: never show "incompatible" for
        # an old/uninstalled version once something is installed, mirroring
        # what a user cares about (their own upgrade path, not every release).
        if installed_version is not None:
            if entry.currently_installed:
                status = "[green]currently installed[/green]"
            elif entry.upgradable:
                status = f"[yellow]upgradable[/yellow] from {installed_version}"
        elif not entry.compatible:
            status = "[grey69]incompatible[/grey69]"
        table.add_row(entry.version, status)

    console.print(title)
    console.print(table)


def get_matching_versions(plugin: Plugin, version_spec: str) -> list[str]:
    wanted_spec = semantic_version.SimpleSpec(version_spec)
    return [
        version
        for version, _ in sorted(plugin.versions.items(), key=lambda p: parse_plugin_version(p[0]), reverse=True)
        if parse_plugin_version(version) in wanted_spec
    ]


def get_all_versions_newest_first(plugin: Plugin) -> list[str]:
    return [
        version
        for version, _ in sorted(plugin.versions.items(), key=lambda p: parse_plugin_version(p[0]), reverse=True)
    ]


def collect_plugin_name_query_result(
    plugins: list[Plugin],
    ref: PluginReference,
    current_version: str,
    current_platform: str,
    installed_records: list[InstalledPluginRecord],
) -> PluginNameQueryResult:
    plugin = get_plugin_by_name(plugins, ref.name, host=ref.host)
    entries, installed_version = collect_version_entries(
        plugin, get_all_versions_newest_first(plugin), current_version, current_platform, installed_records
    )
    return PluginNameQueryResult(
        plugin=collect_plugin_metadata(get_latest_plugin_metadata(plugin)),
        installed_version=installed_version,
        versions=entries,
    )


def render_plugin_name_query_text(result: PluginNameQueryResult) -> None:
    render_plugin_metadata_text(result.plugin)
    render_plugin_versions_text(result.versions, result.installed_version, "available versions:")


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


def collect_download_locations(locations) -> list[DownloadLocationEntry]:
    return [
        DownloadLocationEntry(
            ida_versions=render_ida_versions(location.metadata.plugin.ida_versions),
            platforms=render_platforms(location.metadata.plugin.platforms),
            url=location.url,
        )
        for location in locations
    ]


def collect_plugin_exact_version_query_result(plugin: Plugin, version: str) -> PluginExactVersionQueryResult:
    if version not in plugin.versions:
        raise KeyError(f"version {version} not found for plugin {plugin.name}")

    locations = plugin.versions[version]
    metadata = locations[0].metadata
    return PluginExactVersionQueryResult(
        plugin=collect_plugin_metadata(metadata),
        download_locations=collect_download_locations(locations),
    )


def render_plugin_exact_version_query_text(result: PluginExactVersionQueryResult) -> None:
    render_plugin_metadata_text(result.plugin)

    table = rich.table.Table(show_header=False, box=None)
    table.add_column("IDA version spec", style="default")
    table.add_column("IDA platforms", style="default")
    table.add_column("URL")

    for location in result.download_locations:
        table.add_row(
            "IDA: " + location.ida_versions,
            "platforms: " + location.platforms,
            "URL: " + location.url,
        )

    console.print("download locations:")
    console.print(table)


def collect_plugin_version_range_query_result(
    plugin: Plugin,
    ref: PluginReference,
    current_version: str,
    current_platform: str,
    installed_records: list[InstalledPluginRecord],
) -> PluginVersionRangeQueryResult:
    matching_versions = get_matching_versions(plugin, ref.version_spec)
    if not matching_versions:
        raise KeyError(f"no versions matching {ref.version_spec!r} found for plugin {plugin.name!r}")

    entries, installed_version = collect_version_entries(
        plugin, matching_versions, current_version, current_platform, installed_records
    )
    return PluginVersionRangeQueryResult(
        plugin=collect_plugin_metadata(plugin.versions[matching_versions[0]][0].metadata),
        installed_version=installed_version,
        versions=entries,
    )


def render_plugin_version_range_query_text(result: PluginVersionRangeQueryResult) -> None:
    render_plugin_metadata_text(result.plugin)
    render_plugin_versions_text(result.versions, result.installed_version, "matching versions:")


def collect_plugin_spec_query_result(
    plugins: list[Plugin],
    ref: PluginReference,
    current_version: str,
    current_platform: str,
    installed_records: list[InstalledPluginRecord],
) -> PluginExactVersionQueryResult | PluginVersionRangeQueryResult:
    plugin = get_plugin_by_name(plugins, ref.name, host=ref.host)

    if ref.version_spec.startswith("=="):
        version = ref.version_spec[2:]
        if not version:
            raise ValueError(f"invalid plugin version: {ref.version_spec!r}")
        return collect_plugin_exact_version_query_result(plugin, version)

    return collect_plugin_version_range_query_result(plugin, ref, current_version, current_platform, installed_records)


def render_plugin_spec_query_text(result: PluginExactVersionQueryResult | PluginVersionRangeQueryResult) -> None:
    if isinstance(result, PluginExactVersionQueryResult):
        render_plugin_exact_version_query_text(result)
    else:
        render_plugin_version_range_query_text(result)


def collect_keyword_matches(
    plugins: list[Plugin],
    query: str,
    current_version: str,
    current_platform: str,
    installed_records: list[InstalledPluginRecord],
) -> list[KeywordMatchEntry]:
    matches: list[KeywordMatchEntry] = []

    for plugin in sorted(plugins, key=lambda p: p.name.lower()):
        if not does_plugin_match_query(query or "", plugin):
            continue

        latest_metadata = get_latest_plugin_metadata(plugin)

        if not is_compatible_plugin(plugin, current_platform, current_version):
            matches.append(
                KeywordMatchEntry(
                    name=latest_metadata.plugin.name,
                    version=latest_metadata.plugin.version,
                    repository=latest_metadata.plugin.urls.repository,
                    compatible=False,
                    installed=False,
                    installed_version=None,
                    upgradable=False,
                )
            )
            continue

        latest_compatible_metadata = get_latest_compatible_plugin_metadata(plugin, current_platform, current_version)
        installed_record = find_installed_matching(plugin, installed_records)
        installed_version = installed_record.version if installed_record is not None else None
        upgradable = installed_version is not None and parse_plugin_version(
            latest_compatible_metadata.plugin.version
        ) > parse_plugin_version(installed_version)

        matches.append(
            KeywordMatchEntry(
                name=latest_metadata.plugin.name,
                version=latest_metadata.plugin.version,
                repository=latest_metadata.plugin.urls.repository,
                compatible=True,
                installed=installed_record is not None,
                installed_version=installed_version,
                upgradable=upgradable,
            )
        )

    return matches


def collect_keyword_query_result(
    plugins: list[Plugin],
    query: str,
    current_version: str,
    current_platform: str,
    installed_records: list[InstalledPluginRecord],
) -> KeywordQueryResult:
    return KeywordQueryResult(
        query=query or None,
        results=collect_keyword_matches(plugins, query, current_version, current_platform, installed_records),
    )


def render_keyword_query_text(result: KeywordQueryResult) -> None:
    matches = result.results

    if not matches:
        console.print("[grey69]No plugins found[/grey69]")
        return

    table = rich.table.Table(show_header=False, box=None)
    table.add_column("name", style="blue")
    table.add_column("version", style="default")
    table.add_column("status")
    table.add_column("repo", style="grey69")

    for match in matches:
        if not match.compatible:
            table.add_row(
                f"[grey69]{match.name} (incompatible)[/grey69]",
                f"[grey69]{match.version}[/grey69]",
                "",
                match.repository,
            )
            continue

        status = ""
        if match.upgradable:
            status = f"[yellow]upgradable[/yellow] from {match.installed_version}"
        elif match.installed:
            status = "installed"

        table.add_row(f"[blue]{match.name}[/blue]", match.version, status, match.repository)

    console.print(table)


def _has_exact_name_match(plugins: list[Plugin], name: str) -> bool:
    wanted = name.lower()
    return any(p.name.lower() == wanted for p in plugins)


def resolve_query_reference(plugins: list[Plugin], query: str | None) -> PluginReference | None:
    """Parse `query` as a qualified plugin reference, or None to fall back to keyword search.

    A qualified query (with a host) is always an exact plugin query. An
    unqualified query is only an exact plugin query if it exactly matches a
    known bare name case-insensitively; otherwise it's a keyword/substring
    search. Parse failures (malformed version spec, etc.) also fall back to
    keyword search so unusual user input still works.
    """
    if not query:
        return None

    try:
        ref = parse_plugin_reference(query)
    except ValueError:
        return None

    if ref.host is None and not _has_exact_name_match(plugins, ref.name):
        return None

    return ref


def _dump_result(result: BaseModel) -> dict[str, Any]:
    return result.model_dump(mode="json")


@click.command()
@click.argument("query", required=False)
@click.option("--json", "json_output", is_flag=True, default=False, help="output machine-readable JSON")
@click.pass_context
def search_plugins(ctx, query: str | None = None, json_output: bool = False) -> None:
    """Search for plugins by name, keyword, category, or author."""
    try:
        current_platform = find_current_ida_platform()
        current_version = find_current_ida_version()

        if not json_output:
            console.print(f"[grey69]current platform:[/grey69] {current_platform}")
            console.print(f"[grey69]current version:[/grey69] {current_version}")
            console.print()

        plugin_repo: BasePluginRepo = ctx.obj["plugin_repo"]
        plugins: list[Plugin] = plugin_repo.get_plugins()
        installed_records = get_installed_plugin_records()

        ref = resolve_query_reference(plugins, query)

        if ref is None:
            keyword_result = collect_keyword_query_result(
                plugins, query or "", current_version, current_platform, installed_records
            )
            if json_output:
                print_json(_dump_result(keyword_result))
            else:
                render_keyword_query_text(keyword_result)
            return

        try:
            if ref.version_spec:
                spec_result = collect_plugin_spec_query_result(
                    plugins, ref, current_version, current_platform, installed_records
                )
                if json_output:
                    print_json(_dump_result(spec_result))
                else:
                    render_plugin_spec_query_text(spec_result)
            else:
                name_result = collect_plugin_name_query_result(
                    plugins, ref, current_version, current_platform, installed_records
                )
                if json_output:
                    print_json(_dump_result(name_result))
                else:
                    render_plugin_name_query_text(name_result)

        except AmbiguousPluginReferenceError as e:
            # get_plugin_by_name does not know the user's version spec; attach
            # it here so candidate suggestions render name==1.2.3@repo.
            if ref.version_spec and not e.version_spec:
                e = AmbiguousPluginReferenceError(e.name, e.candidates, ref.version_spec)

            if json_output:
                print_json(_dump_result(collect_ambiguity_error(e)))
                ctx.exit(1)

            render_ambiguity_error_text(e)
            raise click.Abort()

        except (KeyError, ValueError) as e:
            if not json_output:
                raise
            print_json({"error": str(e)})
            ctx.exit(1)

    except MissingCurrentInstallationDirectory:
        explain_missing_current_installation_directory(console)
        raise click.Abort()

    except FailedToDetectIDAVersion:
        explain_failed_to_detect_ida_version(console)
        raise click.Abort()

    except click.Abort:
        raise

    except click.exceptions.Exit:
        raise

    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
