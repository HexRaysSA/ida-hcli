"""Remove the IDA MCP plugin and agent integrations."""

from __future__ import annotations

import logging
import sys

import rich_click as click

from hcli.commands.mcp._agents import (
    PLUGIN_NAME,
    Agent,
    Scope,
    find_agents,
    get_installed_scopes,
    get_scope_description,
    uninstall_agent,
)
from hcli.lib.console import console
from hcli.lib.ida.plugin.exceptions import PluginInUseError
from hcli.lib.ida.plugin.install import is_plugin_installed, sweep_trash, uninstall_plugin

logger = logging.getLogger(__name__)

MANUAL_CONFIGURATION_NOTE = (
    "[dim]If you added the IDA MCP server to an agent's MCP configuration by hand, remove that entry by hand.[/dim]"
)


@click.command()
@click.option("--yes", "-y", is_flag=True, default=False, help="Remove without a confirmation prompt.")
def uninstall(yes: bool) -> None:
    """Remove IDA MCP from IDA and from the agents on PATH that have it.

    The local scope of an agent is the repository in the current directory.
    Run this command in each repository where you installed IDA MCP locally.
    """
    sweep_trash()
    targets: list[tuple[Agent, Scope]] = [
        (agent, scope) for agent in find_agents() for scope in get_installed_scopes(agent)
    ]
    has_ida_plugin = is_plugin_installed(PLUGIN_NAME)

    if not targets and not has_ida_plugin:
        console.print("IDA MCP is not installed for IDA or for an agent on PATH.")
        console.print(MANUAL_CONFIGURATION_NOTE)
        return

    console.print("IDA MCP is installed for:")
    for agent, scope in targets:
        console.print(f"  [blue]{agent.name}[/blue] {get_scope_description(scope)}")
    if has_ida_plugin:
        console.print(f"  IDA plugin [blue]{PLUGIN_NAME}[/blue]")

    if not yes:
        if not sys.stdin.isatty():
            raise click.ClickException("confirmation required: run with --yes to remove without a prompt")
        if not click.confirm("Remove IDA MCP from all of these?", default=False):
            raise click.Abort()

    failures: list[str] = []
    # Remove the agent integrations first, so that no agent is left configured for a removed IDA plugin.
    for agent, scope in targets:
        try:
            uninstall_agent(agent, scope)
        except click.ClickException as e:
            logger.debug("failed to remove IDA MCP from %s: %s", agent.name, e.message)
            failures.append(f"{agent.name} {get_scope_description(scope)}: {e.message}")
        else:
            console.print(
                f"[green]Removed[/green] IDA MCP from [blue]{agent.name}[/blue] {get_scope_description(scope)}"
            )

    if has_ida_plugin:
        try:
            uninstall_plugin(PLUGIN_NAME)
        except PluginInUseError as e:
            failures.append(f"IDA plugin {PLUGIN_NAME}: {e}. Close IDA and run this command again")
        else:
            console.print(f"[green]Uninstalled[/green] plugin: [blue]{PLUGIN_NAME}[/blue]")

    console.print(MANUAL_CONFIGURATION_NOTE)

    if failures:
        raise click.ClickException("could not remove IDA MCP from:\n  " + "\n  ".join(failures))
