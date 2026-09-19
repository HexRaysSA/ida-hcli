"""Plugin uninstall command."""

from __future__ import annotations

import logging
import sys

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida.plugin.components import find_suite_for_component, walk_component_tree_from_directory
from hcli.lib.ida.plugin.dependents import (
    DependencyDeclaration,
    find_companions,
    find_dependents,
    find_remaining_declarers,
)
from hcli.lib.ida.plugin.exceptions import PluginNotInstalledError
from hcli.lib.ida.plugin.install import (
    InstalledPluginRecord,
    find_installed_plugin,
    get_installed_plugin_records,
    sweep_trash,
)
from hcli.lib.ida.plugin.install import uninstall_plugin as uninstall_plugin_impl

logger = logging.getLogger(__name__)


def _print_dependents(record: InstalledPluginRecord, dependents: list[DependencyDeclaration]) -> None:
    console.print(f"These installed plugins depend on [blue]{record.name}[/blue]:")
    for declaration in dependents:
        verb = "requires" if declaration.spec.required else "optionally uses"
        console.print(f"  {declaration.describe_declarer()} {verb} {declaration.target}")


def _print_components(record: InstalledPluginRecord) -> bool:
    try:
        tree = walk_component_tree_from_directory(record.path)
    except ValueError:
        return False
    if not tree:
        return False
    console.print(f"[blue]{record.name}[/blue] manages these plugins:")
    for _, comp_meta in tree:
        console.print(f"  {comp_meta.plugin.name:30s} {comp_meta.plugin.version}")
    return True


def _remove_companions(companions: list[InstalledPluginRecord], yes: bool) -> None:
    remaining = get_installed_plugin_records()
    removable: list[InstalledPluginRecord] = []
    console.print("These plugins were listed as dependencies:")
    for companion in companions:
        declarers = find_remaining_declarers(remaining, companion.name)
        if declarers:
            still = ", ".join(sorted({d.describe_declarer() for d in declarers}))
            console.print(f"  {companion.name}=={companion.version}  (kept: still declared by {still})")
        else:
            console.print(f"  {companion.name}=={companion.version}")
            removable.append(companion)

    if not removable:
        return

    if yes:
        should_remove = True
    elif sys.stdin.isatty():
        should_remove = click.confirm("Remove them too?", default=False)
    else:
        console.print("Remove them manually if no longer needed.")
        return

    if not should_remove:
        return

    for companion in removable:
        try:
            uninstall_plugin_impl(companion.name)
            console.print(f"  [green]Uninstalled[/green] dependency: [blue]{companion.name}[/blue]")
        except Exception as e:
            logger.debug("failed to uninstall dependency %s: %s", companion.name, e, exc_info=True)
            console.print(f"  [red]Failed[/red] to uninstall dependency {companion.name}: {e}")


@click.command()
@click.argument("plugin")
@click.option("--yes", "-y", is_flag=True, default=False, help="Confirm all prompts automatically.")
def uninstall_plugin(plugin: str, yes: bool) -> None:
    """Remove an installed plugin."""
    companions: list[InstalledPluginRecord] = []
    try:
        sweep_trash()

        suite_record = find_suite_for_component(plugin)
        if suite_record is not None:
            console.print(f"[red]{plugin}[/red] is a component of [blue]{suite_record.name}[/blue].")
            raise click.Abort()

        try:
            record = find_installed_plugin(plugin)
        except PluginNotInstalledError:
            record = None

        if record is not None:
            records = get_installed_plugin_records()
            dependents = find_dependents(records, record)
            companions = find_companions(records, record)
            if dependents:
                _print_dependents(record, dependents)
            has_components = _print_components(record)

            interactive = sys.stdin.isatty()
            if (dependents or has_components) and not yes and interactive:
                prompt = "Uninstall all?" if has_components else "Uninstall anyway?"
                if not click.confirm(prompt, default=True):
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

    if companions:
        _remove_companions(companions, yes)
