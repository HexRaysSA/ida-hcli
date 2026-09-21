"""Plugin uninstall command."""

from __future__ import annotations

import logging
import sys

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida.plugin.components import find_suite_for_component, walk_component_tree_from_directory
from hcli.lib.ida.plugin.exceptions import PluginNotInstalledError
from hcli.lib.ida.plugin.install import (
    find_installed_plugin,
    sweep_trash,
)
from hcli.lib.ida.plugin.install import uninstall_plugin as uninstall_plugin_impl
from hcli.lib.ida.plugin.reference import DependencyEntry

logger = logging.getLogger(__name__)


@click.command()
@click.argument("plugin")
@click.option("--yes", "-y", is_flag=True, default=False, help="Confirm all prompts automatically.")
def uninstall_plugin(plugin: str, yes: bool) -> None:
    """Remove an installed plugin."""
    dep_names: list[str] = []
    try:
        sweep_trash()

        suite_record = find_suite_for_component(plugin)
        if suite_record is not None:
            console.print(f"[red]{plugin}[/red] is a component of [blue]{suite_record.name}[/blue].")
            raise click.Abort()

        try:
            record = find_installed_plugin(plugin)
            for entry in record.metadata.plugin.dependencies:
                if isinstance(entry, DependencyEntry):
                    dep_names.append(entry.reference.name)
        except PluginNotInstalledError:
            pass
        else:
            if record.metadata.plugin.components:
                try:
                    tree = walk_component_tree_from_directory(record.path)
                except ValueError:
                    tree = []

                if tree:
                    console.print(f"[blue]{plugin}[/blue] manages these plugins:")
                    for _, comp_meta in tree:
                        console.print(f"  {comp_meta.plugin.name:30s} {comp_meta.plugin.version}")

                    interactive = sys.stdin.isatty()
                    if not yes and interactive and not click.confirm("Uninstall all?", default=True):
                        raise click.Abort()

        uninstall_plugin_impl(plugin)
    except PluginNotInstalledError as e:
        console.print(f"[red]{e}[/red]")
        raise click.Abort()
    except Exception as e:
        if isinstance(e, click.Abort):
            raise
        logger.debug("failed to uninstall: %s", e, exc_info=True)
        console.print(f"[red]uninstall failed: {e}[/red]")
        raise click.Abort()

    console.print(f"[green]Uninstalled[/green] plugin: [blue]{plugin}[/blue]")

    if not dep_names:
        return

    installed_deps: list[tuple[str, str | None]] = []
    for name in dep_names:
        try:
            dep_record = find_installed_plugin(name)
            installed_deps.append((name, dep_record.version))
        except PluginNotInstalledError:
            pass
    if not installed_deps:
        return

    console.print("These plugins were listed as dependencies:")
    for name, version in installed_deps:
        version_str = f"=={version}" if version else ""
        console.print(f"  {name}{version_str}")

    interactive = sys.stdin.isatty()
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
