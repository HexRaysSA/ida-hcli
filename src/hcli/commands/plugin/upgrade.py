from __future__ import annotations

import logging
from pathlib import Path

import httpx
import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida import (
    FailedToDetectIDAVersion,
    MissingCurrentInstallationDirectory,
    explain_failed_to_detect_ida_version,
    explain_missing_current_installation_directory,
    find_current_ida_platform,
    find_current_ida_version,
)
from hcli.lib.ida.plugin import get_metadata_from_plugin_archive
from hcli.lib.ida.plugin.exceptions import PluginNotInstalledError
from hcli.lib.ida.plugin.install import find_installed_plugin, upgrade_plugin_archive
from hcli.lib.ida.plugin.reference import normalize_plugin_host, parse_plugin_reference
from hcli.lib.ida.plugin.repo import BasePluginRepo

logger = logging.getLogger(__name__)


@click.command()
@click.pass_context
@click.argument("plugin")
@click.option(
    "--no-build-isolation",
    is_flag=True,
    default=False,
    help="Disable pip build isolation when installing Python dependencies",
)
def upgrade_plugin(ctx, plugin: str, no_build_isolation: bool) -> None:
    """Upgrade an installed plugin to the latest compatible version."""
    plugin_spec = plugin
    try:
        current_ida_platform = find_current_ida_platform()
        current_ida_version = find_current_ida_version()

        if Path(plugin_spec).exists() and plugin_spec.endswith(".zip"):
            raise ValueError("cannot upgrade using local file; uninstall/reinstall instead")

        if plugin_spec.startswith("file://"):
            raise ValueError("cannot upgrade using local file; uninstall/reinstall instead")

        if plugin_spec.startswith("https://"):
            raise ValueError("cannot upgrade using URL; uninstall/reinstall instead")

        try:
            ref = parse_plugin_reference(plugin_spec)
        except ValueError as e:
            raise click.BadParameter(f"invalid plugin reference: {plugin_spec!r}: {e}")

        # Resolve the installed plugin first so we can anchor the upgrade to
        # the repository the user currently has installed. This avoids
        # switching repositories implicitly and also resolves the host for
        # bare-name upgrades even when the repository has a colliding name.
        try:
            installed = find_installed_plugin(ref.name)
        except PluginNotInstalledError:
            console.print(f"[red]Error[/red]: plugin '{ref.name}' is not installed")
            raise click.Abort()

        if ref.host is not None and normalize_plugin_host(installed.host) != normalize_plugin_host(ref.host):
            console.print(
                f"[red]Error[/red]: installed plugin '{installed.name}' comes from {installed.host}, not {ref.host}"
            )
            console.print(
                "Upgrade cannot switch repositories. Uninstall first, then install the other qualified plugin."
            )
            raise click.Abort()

        # Anchor the lookup to the installed host regardless of whether the
        # user supplied it. This is what makes bare-name upgrades work even
        # when the repository has a colliding name.
        bare_spec = ref.name + ref.version_spec
        logger.info("finding plugin in repository")
        plugin_repo: BasePluginRepo = ctx.obj["plugin_repo"]
        try:
            plugin_name, buf = plugin_repo.fetch_compatible_plugin_from_spec(
                bare_spec, current_ida_platform, current_ida_version, host=installed.host
            )
        except (httpx.ConnectError, httpx.TimeoutException):
            console.print("[red]Cannot connect to plugin repository - network unavailable.[/red]")
            console.print("Please check your internet connection.")
            raise click.Abort()

        upgrade_plugin_archive(buf, plugin_name, no_build_isolation=no_build_isolation)

        _, metadata = get_metadata_from_plugin_archive(buf, plugin_name)

        console.print(f"[green]Installed[/green] plugin: [blue]{plugin_name}[/blue]=={metadata.plugin.version}")
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
