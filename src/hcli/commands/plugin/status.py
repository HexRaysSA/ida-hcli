"""Plugin status command."""

from __future__ import annotations

import logging

import rich.table
import rich_click as click

from hcli.env import ENV
from hcli.lib.console import console
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


@click.command()
@click.argument("plugins", nargs=-1)
@click.pass_context
def get_plugin_status(ctx, plugins: tuple[str, ...]) -> None:
    """Show installed plugins and their upgrade status.

    If one or more PLUGINS are given, show status for just those plugins,
    and exit with a non-zero status if any of them isn't installed.
    """
    plugin_repo: BasePluginRepo = ctx.obj["plugin_repo"]
    not_found_names: list[str] = []
    try:
        current_platform = find_current_ida_platform()
        current_ida_version = find_current_ida_version()

        table = rich.table.Table(show_header=False, box=None)
        table.add_column("name", style="blue")
        table.add_column("version", style="default")
        table.add_column("status")

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

        for record in installed_records:
            status = ""
            try:
                # Anchor the repository lookup on the installed plugin's host so a
                # colliding plugin name in the repository does not raise ambiguity
                # or pick the wrong variant.
                location = plugin_repo.find_compatible_plugin_from_spec(
                    record.name, current_platform, current_ida_version, host=record.host
                )
                if parse_plugin_version(location.metadata.plugin.version) > parse_plugin_version(record.version):
                    status = f"upgradable to [yellow]{location.metadata.plugin.version}[/yellow]"
            except (ValueError, KeyError, AmbiguousPluginReferenceError):
                # AmbiguousPluginReferenceError should not escape this command.
                # The installed plugin's own metadata carries its host, so
                # anchoring on that host above should normally resolve the
                # collision; if it does not, treat the plugin as absent from
                # the repository rather than crashing status.
                status = "[yellow]not found in repository[/yellow]"

            table.add_row(record.name, record.version, status)

        has_incompatible_plugins = False
        has_legacy_plugins = False
        if not plugins:
            plugin_directory = get_plugins_directory()
            for path, metadata in get_installed_minimal_plugins():
                plugin_path = path.parent.relative_to(plugin_directory)
                table.add_row(
                    f"[grey69](incompatible)[/grey69] [blue]{metadata.plugin.name}[/blue]",
                    metadata.plugin.version or "",
                    f"[grey69]found at: $IDAPLUGINS/[/grey69]{plugin_path}/",
                )
                has_incompatible_plugins = True

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
        elif not plugins:
            console.print("[grey69]No plugins found[/grey69]")

        for name in not_found_names:
            console.print(f"[red]Not installed[/red]: {name}")

        if has_incompatible_plugins:
            console.print()
            console.print("[yellow]Incompatible plugins[/yellow] don't work with this version of hcli.")
            console.print(
                f"[dim]They might be broken or outdated. Try using `{ENV.HCLI_BINARY_NAME} plugin lint /path/to/plugin`.[/dim]"
            )

        if has_legacy_plugins:
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
