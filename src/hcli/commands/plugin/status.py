"""Plugin status command."""

from __future__ import annotations

import logging
from typing import Literal

import rich.table
import rich_click as click
from pydantic import BaseModel

from hcli.env import ENV
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
from hcli.lib.ida.plugin.exceptions import AmbiguousPluginReferenceError
from hcli.lib.ida.plugin.install import (
    InstalledPluginRecord,
    find_installed_plugin_in,
    get_installed_legacy_plugins,
    get_installed_minimal_plugins,
    get_installed_plugin_records,
    get_plugins_directory,
)
from hcli.lib.ida.plugin.repo import BasePluginRepo

logger = logging.getLogger(__name__)


class InstalledPluginStatusEntry(BaseModel):
    name: str
    version: str
    installed: Literal[True] = True
    kind: Literal["installed"] = "installed"
    upgrade_checked: bool
    in_repository: bool | None
    upgradable_to: str | None


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


def _collect_installed_entry(
    plugin_repo: BasePluginRepo,
    record: InstalledPluginRecord,
    current_platform: str,
    current_ida_version: str,
    skip_upgrade_check: bool,
) -> InstalledPluginStatusEntry:
    if skip_upgrade_check:
        return InstalledPluginStatusEntry(
            name=record.name,
            version=record.version,
            upgrade_checked=False,
            in_repository=None,
            upgradable_to=None,
        )

    try:
        location = plugin_repo.find_compatible_plugin_from_spec(
            record.name, current_platform, current_ida_version, host=record.host
        )
        latest_version = location.metadata.plugin.version
        return InstalledPluginStatusEntry(
            name=record.name,
            version=record.version,
            upgrade_checked=True,
            in_repository=True,
            upgradable_to=(
                latest_version if parse_plugin_version(latest_version) > parse_plugin_version(record.version) else None
            ),
        )
    except (ValueError, KeyError, AmbiguousPluginReferenceError):
        return InstalledPluginStatusEntry(
            name=record.name,
            version=record.version,
            upgrade_checked=True,
            in_repository=False,
            upgradable_to=None,
        )


def _render_status_row(table: rich.table.Table, entry: PluginStatusEntry) -> None:
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

    table.add_row(entry.name, entry.version, status)


def collect_status_report(
    plugin_repo: BasePluginRepo,
    plugins: tuple[str, ...],
    skip_upgrade_check: bool,
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

    entries: list[PluginStatusEntry] = [
        _collect_installed_entry(plugin_repo, record, current_platform, current_ida_version, skip_upgrade_check)
        for record in installed_records
    ]

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


def render_status_report_text(report: StatusReport, plugins_filter: tuple[str, ...]) -> None:
    table = rich.table.Table(show_header=False, box=None)
    table.add_column("name", style="blue")
    table.add_column("version", style="default")
    table.add_column("status")

    not_found_names: list[str] = []
    has_incompatible = False
    has_legacy = False

    for entry in report.plugins:
        if isinstance(entry, NotFoundPluginStatusEntry):
            not_found_names.append(entry.name)
            continue
        if isinstance(entry, IncompatiblePluginStatusEntry):
            has_incompatible = True
        elif isinstance(entry, LegacyPluginStatusEntry):
            has_legacy = True
        _render_status_row(table, entry)

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
            f"[dim]They might be broken or outdated. Try using `{ENV.HCLI_BINARY_NAME} plugin lint /path/to/plugin`.[/dim]"
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
@click.pass_context
def get_plugin_status(ctx, plugins: tuple[str, ...], skip_upgrade_check: bool, json_output: bool) -> None:
    """Show installed plugins and their upgrade status.

    If one or more PLUGINS are given, show status for just those plugins,
    and exit with a non-zero status if any of them isn't installed.
    """
    plugin_repo: BasePluginRepo = ctx.obj["plugin_repo"]
    try:
        report = collect_status_report(plugin_repo, plugins, skip_upgrade_check)

        if json_output:
            render_status_report_json(report)
        else:
            render_status_report_text(report, plugins)

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
