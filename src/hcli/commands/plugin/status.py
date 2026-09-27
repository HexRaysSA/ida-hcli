"""Plugin status command."""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable
from typing import Literal

import rich.table
import rich_click as click
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
from hcli.lib.ida.plugin import parse_plugin_version
from hcli.lib.ida.plugin.components import walk_component_tree_from_directory
from hcli.lib.ida.plugin.exceptions import AmbiguousPluginReferenceError
from hcli.lib.ida.plugin.install import (
    InstalledPluginRecord,
    find_installed_plugin_in,
    get_installed_legacy_plugins,
    get_installed_minimal_plugins,
    get_installed_plugin_records,
    get_plugins_directory,
)
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin
from hcli.lib.util.io import get_hcli_display_command

from ._listing import get_repository_group_sort_key, render_plugin_label

logger = logging.getLogger(__name__)


class ComponentInfo(BaseModel):
    name: str
    version: str


class InstalledPluginStatusEntry(BaseModel):
    name: str
    version: str
    installed: Literal[True] = True
    kind: Literal["installed"] = "installed"
    upgrade_checked: bool
    in_repository: bool | None
    upgradable_to: str | None
    # Which configured repository serves this plugin. None when the upgrade
    # check is skipped, when no repository serves it, or under --repo.
    repo: str | None = None
    components: list[ComponentInfo] | None = None


class NotFoundPluginStatusEntry(BaseModel):
    name: str
    installed: Literal[False] = False


class IncompatiblePluginStatusEntry(BaseModel):
    name: str
    version: str | None
    installed: Literal[True] = True
    kind: Literal["incompatible"] = "incompatible"
    path: str


class LegacyPluginStatusEntry(BaseModel):
    name: str
    version: None = None
    installed: Literal[True] = True
    kind: Literal["legacy"] = "legacy"
    path: str


PluginStatusEntry = (
    InstalledPluginStatusEntry | NotFoundPluginStatusEntry | IncompatiblePluginStatusEntry | LegacyPluginStatusEntry
)


class StatusReport(BaseModel):
    plugins: list[PluginStatusEntry]


def _collect_components(record: InstalledPluginRecord) -> list[ComponentInfo] | None:
    if not record.metadata.plugin.components:
        return None
    try:
        tree = walk_component_tree_from_directory(record.path)
        return [ComponentInfo(name=m.plugin.name, version=m.plugin.version) for _, m in tree]
    except ValueError:
        return None


def _collect_installed_entry(
    plugin_repo: BasePluginRepo,
    record: InstalledPluginRecord,
    current_platform: str,
    current_ida_version: str,
    skip_upgrade_check: bool,
    repo_of: Callable[[Plugin], str | None] | None = None,
) -> InstalledPluginStatusEntry:
    components = _collect_components(record)

    if skip_upgrade_check:
        return InstalledPluginStatusEntry(
            name=record.name,
            version=record.version,
            upgrade_checked=False,
            in_repository=None,
            upgradable_to=None,
            components=components,
        )

    try:
        location = plugin_repo.find_compatible_plugin_from_spec(
            record.name, current_platform, current_ida_version, host=record.host
        )
        latest_version = location.metadata.plugin.version
        repo = repo_of(plugin_repo.get_plugin_by_name(record.name, host=record.host)) if repo_of is not None else None
        return InstalledPluginStatusEntry(
            name=record.name,
            version=record.version,
            upgrade_checked=True,
            in_repository=True,
            upgradable_to=(
                latest_version if parse_plugin_version(latest_version) > parse_plugin_version(record.version) else None
            ),
            repo=repo,
            components=components,
        )
    except (ValueError, KeyError, AmbiguousPluginReferenceError):
        return InstalledPluginStatusEntry(
            name=record.name,
            version=record.version,
            upgrade_checked=True,
            in_repository=False,
            upgradable_to=None,
            components=components,
        )


def _render_status_row(table: rich.table.Table, entry: PluginStatusEntry, default_repo: str | None) -> None:
    if isinstance(entry, IncompatiblePluginStatusEntry):
        table.add_row(
            f"[grey69](incompatible)[/grey69] [blue]{entry.name}[/blue]",
            entry.version or "",
            f"[grey69]found at: $IDAPLUGINS/[/grey69]{entry.path}",
        )
        return

    if isinstance(entry, LegacyPluginStatusEntry):
        table.add_row(
            f"[grey69](legacy)[/grey69] [blue]{entry.name}[/blue]",
            "",
            f"[grey69]found at: $IDAPLUGINS/[/grey69]{entry.path}",
        )
        return

    if isinstance(entry, NotFoundPluginStatusEntry):
        return

    if not entry.upgrade_checked:
        status = "[dim]skipped[/dim]"
    elif not entry.in_repository:
        status = "[yellow]not found in repository[/yellow]"
    elif entry.upgradable_to:
        status = f"upgradable to [yellow]{entry.upgradable_to}[/yellow]"
    else:
        status = ""

    if entry.components:
        n = len(entry.components)
        comp_label = f"({n} component{'s' if n != 1 else ''})"
        status = f"{status}  {comp_label}".strip() if status else comp_label

    table.add_row(render_plugin_label(entry.name, entry.repo, default_repo), entry.version, status)


def collect_status_report(
    plugin_repo: BasePluginRepo,
    plugins: tuple[str, ...],
    skip_upgrade_check: bool,
    repo_of: Callable[[Plugin], str | None] | None = None,
) -> StatusReport:
    current_platform = find_current_ida_platform()
    current_ida_version = find_current_ida_version()

    all_records = get_installed_plugin_records()

    not_found_names: list[str] = []
    if plugins:
        installed_records = []
        for name in plugins:
            record = find_installed_plugin_in(all_records, name)
            if record is None:
                not_found_names.append(name)
            else:
                installed_records.append(record)
    else:
        installed_records = all_records

    installed_entries = [
        _collect_installed_entry(
            plugin_repo, record, current_platform, current_ida_version, skip_upgrade_check, repo_of=repo_of
        )
        for record in installed_records
    ]
    entries: list[PluginStatusEntry] = sorted(
        installed_entries, key=lambda e: (get_repository_group_sort_key(e.repo), e.name.lower())
    )

    entries.extend(NotFoundPluginStatusEntry(name=name) for name in not_found_names)

    if not plugins:
        plugin_directory = get_plugins_directory()
        for path, metadata in get_installed_minimal_plugins():
            plugin_path = path.parent.relative_to(plugin_directory)
            entries.append(
                IncompatiblePluginStatusEntry(
                    name=metadata.plugin.name,
                    version=metadata.plugin.version or None,
                    path=f"{plugin_path}/",
                )
            )

        for path in get_installed_legacy_plugins():
            entries.append(
                LegacyPluginStatusEntry(
                    name=path.name,
                    path=path.name,
                )
            )

    return StatusReport(plugins=entries)


def _get_entry_repo(entry: PluginStatusEntry) -> str | None:
    return entry.repo if isinstance(entry, InstalledPluginStatusEntry) else None


def render_status_report_text(
    report: StatusReport,
    plugins_filter: tuple[str, ...],
    *,
    show_components: bool = False,
    default_repo: str | None = None,
) -> None:
    table = rich.table.Table(show_header=False, box=None)
    table.add_column("name", style="blue")
    table.add_column("version", style="default")
    table.add_column("status")

    has_incompatible = False
    has_legacy = False

    not_found_names = [e.name for e in report.plugins if isinstance(e, NotFoundPluginStatusEntry)]
    listed = [e for e in report.plugins if not isinstance(e, NotFoundPluginStatusEntry)]

    for i, (_, group) in enumerate(itertools.groupby(listed, key=_get_entry_repo)):
        if i:
            table.add_row()

        for entry in group:
            if isinstance(entry, IncompatiblePluginStatusEntry):
                has_incompatible = True
            elif isinstance(entry, LegacyPluginStatusEntry):
                has_legacy = True
            _render_status_row(table, entry, default_repo)
            if show_components and isinstance(entry, InstalledPluginStatusEntry) and entry.components:
                for comp in entry.components:
                    table.add_row(f"  [dim]{comp.name}[/dim]", comp.version, "[dim](component)[/dim]")

    if table.row_count:
        console.print(table)
    elif not plugins_filter:
        console.print("[grey69]No plugins found[/grey69]")

    for name in not_found_names:
        console.print(f"[red]Not installed[/red]: {name}")

    if has_incompatible:
        console.print()
        console.print("[yellow]Incompatible plugins[/yellow] don't work with this version of hcli.")
        console.print(
            f"[dim]They might be broken or outdated. Try using `{get_hcli_display_command()} plugin lint /path/to/plugin`.[/dim]"
        )

    if has_legacy:
        console.print()
        console.print("[yellow]Legacy plugins[/yellow] are old, single-file plugins.")
        console.print("They aren't managed by hcli. Try finding an updated version in the plugin repository.")


def render_status_report_json(report: StatusReport) -> None:
    print_json(report.model_dump(mode="json"))


@click.command()
@click.argument("plugins", nargs=-1)
@click.option(
    "--skip-upgrade-check",
    is_flag=True,
    default=False,
    help="skip the per-plugin upgrade check against the plugin repository",
)
@click.option("--json", "json_output", is_flag=True, default=False, help="output machine-readable JSON")
@click.option(
    "--show-components",
    is_flag=True,
    default=False,
    help="expand plugin suites to show their bundled components",
)
@click.pass_context
def get_plugin_status(
    ctx, plugins: tuple[str, ...], skip_upgrade_check: bool, json_output: bool, show_components: bool
) -> None:
    """Show installed plugins and their upgrade status.

    If one or more PLUGINS are given, show status for just those plugins,
    and exit with a non-zero status if any of them isn't installed.
    """
    plugin_repo: BasePluginRepo = ctx.obj["plugin_repo"]
    aggregate = ctx.obj.get("plugin_repos")
    repo_of = aggregate.repo_of if aggregate is not None else None
    try:
        report = collect_status_report(plugin_repo, plugins, skip_upgrade_check, repo_of=repo_of)

        if json_output:
            render_status_report_json(report)
        else:
            render_status_report_text(
                report, plugins, show_components=show_components, default_repo=ctx.obj.get("default_plugin_repo")
            )

    except MissingCurrentInstallationDirectory:
        explain_missing_current_installation_directory(console)
        raise click.Abort()

    except FailedToDetectIDAVersion:
        explain_failed_to_detect_ida_version(console)
        raise click.Abort()

    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()

    has_not_found = any(isinstance(e, NotFoundPluginStatusEntry) for e in report.plugins)
    if has_not_found:
        ctx.exit(1)
