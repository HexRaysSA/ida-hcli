"""Plugin uninstall command."""

from __future__ import annotations

import logging
import sys

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida.plugin.exceptions import PluginNotInstalledError
from hcli.lib.ida.plugin.install import (
    find_installed_plugin,
    is_plugin_installed,
    sweep_trash,
)
from hcli.lib.ida.plugin.install import uninstall_plugin as uninstall_plugin_impl

logger = logging.getLogger(__name__)


@click.command()
@click.argument("plugin")
@click.option("--yes", "-y", is_flag=True, default=False, help="Confirm all prompts automatically.")
def uninstall_plugin(plugin: str, yes: bool) -> None:
    """Remove an installed plugin."""
    dep_names: list[str] = []
    try:
        sweep_trash()

        try:
            record = find_installed_plugin(plugin)
            dep_names = list(record.metadata.plugin.dependencies)
        except PluginNotInstalledError:
            pass

        uninstall_plugin_impl(plugin)
    except PluginNotInstalledError as e:
        console.print(f"[red]{e}[/red]")
        raise click.Abort()
    except Exception as e:
        logger.debug("failed to uninstall: %s", e, exc_info=True)
        console.print(f"[red]uninstall failed: {e}[/red]")
        raise click.Abort()

    console.print(f"[green]Uninstalled[/green] plugin: [blue]{plugin}[/blue]")

    if not dep_names:
        return

    installed_deps = [(name, _get_installed_version(name)) for name in dep_names if is_plugin_installed(name)]
    if not installed_deps:
        return

    console.print("These plugins were listed as dependencies:")
    for name, version in installed_deps:
        version_str = f"    {version}" if version else ""
        console.print(f"  {name}{version_str}")

    interactive = sys.stdin.isatty() and not yes
    if yes:
        should_remove = True
    elif interactive:
        should_remove = click.confirm("Remove them too?", default=False)
    else:
        console.print("Remove them manually if no longer needed.")
        return

    if not should_remove:
        return

    for name, _ in installed_deps:
        try:
            uninstall_plugin_impl(name)
            console.print(f"  [green]Uninstalled[/green] dependency: [blue]{name}[/blue]")
        except Exception as e:
            logger.debug("failed to uninstall dependency %s: %s", name, e, exc_info=True)
            console.print(f"  [red]Failed[/red] to uninstall dependency {name}: {e}")


def _get_installed_version(name: str) -> str | None:
    try:
        record = find_installed_plugin(name)
        return record.version
    except PluginNotInstalledError:
        return None
