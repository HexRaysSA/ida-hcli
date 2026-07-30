"""Plugin status command."""

from __future__ import annotations

import logging

import rich.table
import rich_click as click

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
    find_installed_plugin_in,
    get_installed_legacy_plugins,
    get_installed_minimal_plugins,
    get_installed_plugin_records,
    get_plugins_directory,
)
from hcli.lib.ida.plugin.repo import BasePluginRepo

logger = logging.getLogger(__name__)


def _build_installed_entry(
    plugin_repo: BasePluginRepo,
    record,
    current_platform: str,
    current_ida_version: str,
    offline: bool,
) -> dict:
    entry = {"name": record.name, "version": record.version, "installed": True, "kind": "installed"}

    if offline:
        entry["upgrade_checked"] = False
        entry["in_repository"] = None
        entry["upgradable_to"] = None
        return entry

    try:
        # Anchor the repository lookup on the installed plugin's host so a
        # colliding plugin name in the repository does not raise ambiguity
        # or pick the wrong variant.
        location = plugin_repo.find_compatible_plugin_from_spec(
            record.name, current_platform, current_ida_version, host=record.host
        )
        entry["upgrade_checked"] = True
        entry["in_repository"] = True
        latest_version = location.metadata.plugin.version
        entry["upgradable_to"] = (
            latest_version if parse_plugin_version(latest_version) > parse_plugin_version(record.version) else None
        )
    except (ValueError, KeyError, AmbiguousPluginReferenceError):
        # AmbiguousPluginReferenceError should not escape this command. The
        # installed plugin's own metadata carries its host, so anchoring on
        # that host above should normally resolve the collision; if it does
        # not, treat the plugin as absent from the repository rather than
        # crashing status.
        entry["upgrade_checked"] = True
        entry["in_repository"] = False
        entry["upgradable_to"] = None

    return entry


def _render_status_row(table: rich.table.Table, entry: dict) -> None:
    kind = entry.get("kind")

    if kind == "incompatible":
        table.add_row(
            f"[grey69](incompatible)[/grey69] [blue]{entry['name']}[/blue]",
            entry["version"] or "",
            f"[grey69]found at: $IDAPLUGINS/[/grey69]{entry['path']}",
        )
        return

    if kind == "legacy":
        table.add_row(
            f"[grey69](legacy)[/grey69] [blue]{entry['name']}[/blue]",
            "",
            f"[grey69]found at: $IDAPLUGINS/[/grey69]{entry['path']}",
        )
        return

    if not entry["upgrade_checked"]:
        status = "[dim]skipped (offline)[/dim]"
    elif not entry["in_repository"]:
        status = "[yellow]not found in repository[/yellow]"
    elif entry["upgradable_to"]:
        status = f"upgradable to [yellow]{entry['upgradable_to']}[/yellow]"
    else:
        status = ""

    table.add_row(entry["name"], entry["version"], status)


@click.command()
@click.argument("plugins", nargs=-1)
@click.option(
    "--offline",
    is_flag=True,
    default=False,
    help="skip the per-plugin upgrade check against the plugin repository",
)
@click.option("--json", "json_output", is_flag=True, default=False, help="output machine-readable JSON")
@click.pass_context
def get_plugin_status(ctx, plugins: tuple[str, ...], offline: bool, json_output: bool) -> None:
    """Show installed plugins and their upgrade status.

    If one or more PLUGINS are given, show status for just those plugins,
    and exit with a non-zero status if any of them isn't installed.
    """
    plugin_repo: BasePluginRepo = ctx.obj["plugin_repo"]
    not_found_names: list[str] = []
    try:
        current_platform = find_current_ida_platform()
        current_ida_version = find_current_ida_version()

        all_records = get_installed_plugin_records()
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

        entries = [
            _build_installed_entry(plugin_repo, record, current_platform, current_ida_version, offline)
            for record in installed_records
        ]
        not_found_entries = [{"name": name, "installed": False} for name in not_found_names]

        incompatible_entries = []
        legacy_entries = []
        if not plugins:
            plugin_directory = get_plugins_directory()
            for path, metadata in get_installed_minimal_plugins():
                plugin_path = path.parent.relative_to(plugin_directory)
                incompatible_entries.append(
                    {
                        "name": metadata.plugin.name,
                        "version": metadata.plugin.version or None,
                        "installed": True,
                        "kind": "incompatible",
                        "path": f"{plugin_path}/",
                    }
                )

            for path in get_installed_legacy_plugins():
                plugin_path = path.parent.relative_to(plugin_directory)
                legacy_entries.append(
                    {
                        "name": path.name,
                        "version": None,
                        "installed": True,
                        "kind": "legacy",
                        "path": f"{path.name}",
                    }
                )

        if json_output:
            print_json({"plugins": entries + not_found_entries + incompatible_entries + legacy_entries})
        else:
            table = rich.table.Table(show_header=False, box=None)
            table.add_column("name", style="blue")
            table.add_column("version", style="default")
            table.add_column("status")

            for entry in entries + incompatible_entries + legacy_entries:
                _render_status_row(table, entry)

            if table.row_count:
                console.print(table)
            elif not plugins:
                console.print("[grey69]No plugins found[/grey69]")

            for name in not_found_names:
                console.print(f"[red]Not installed[/red]: {name}")

            if incompatible_entries:
                console.print()
                console.print("[yellow]Incompatible plugins[/yellow] don't work with this version of hcli.")
                console.print(
                    f"[dim]They might be broken or outdated. Try using `{ENV.HCLI_BINARY_NAME} plugin lint /path/to/plugin`.[/dim]"
                )

            if legacy_entries:
                # TODO: suggest plugins in the repo, by maintaining a translation list from filename to package name
                console.print()
                console.print("[yellow]Legacy plugins[/yellow] are old, single-file plugins.")
                console.print("They aren't managed by hcli. Try finding an updated version in the plugin repository.")

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

    if not_found_names:
        ctx.exit(1)
