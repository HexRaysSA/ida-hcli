"""Undo `hcli mcp install`: remove the agent integrations and the IDA plugin."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

import rich_click as click

from hcli.lib.console import console, stderr_console
from hcli.lib.ida.plugin.exceptions import PluginInUseError, PluginNotInstalledError
from hcli.lib.ida.plugin.install import find_installed_plugin, sweep_trash
from hcli.lib.ida.plugin.install import uninstall_plugin as uninstall_plugin_impl

from .install import (
    _AGENT_DEFINITIONS,
    PLUGIN_NAME,
    Agent,
    Scope,
    _agent_scopes,
    _find_agents,
    _integration,
    _run_checked,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Installation:
    agent: Agent
    scope: Scope

    def describe(self) -> str:
        if not self.agent.supports_local:
            return self.agent.name
        where = "this repository" if self.scope == "local" else "current user"
        return f"{self.agent.name} ({where})"


def _find_installations(agents: list[Agent], scope: Scope | None) -> list[Installation]:
    installations: list[Installation] = []
    for agent in agents:
        integration = _integration(agent)
        for candidate in _agent_scopes(agent):
            if scope is not None and candidate != scope:
                continue
            if integration.is_installed(agent, candidate):
                installations.append(Installation(agent, candidate))
    return installations


def _is_ida_plugin_installed() -> bool:
    try:
        find_installed_plugin(PLUGIN_NAME)
    except PluginNotInstalledError:
        return False
    except Exception as e:  # no IDA user directory, unreadable records, ...
        logger.debug("could not look up the %s IDA plugin: %s", PLUGIN_NAME, e, exc_info=True)
        return False
    return True


def _plan(installations: list[Installation], remove_marketplace: bool, remove_plugin: bool) -> list[str]:
    steps: list[str] = []
    for installation in installations:
        agent = installation.agent
        integration = _integration(agent)
        steps.append(f"{agent.command} {' '.join(integration.uninstall_args(installation.scope))}")
        if remove_marketplace and integration.marketplace_remove_args is not None:
            steps.append(f"{agent.command} {' '.join(integration.marketplace_remove_args(installation.scope))}")
    if remove_plugin:
        steps.append(f"hcli plugin uninstall {PLUGIN_NAME}")
    return steps


def _confirm(yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise click.ClickException("refusing to uninstall without a terminal to confirm; pass --yes")
    if not click.confirm("Proceed?", default=True):
        raise click.Abort()


def _uninstall_ida_plugin() -> str | None:
    """Remove the IDA-side plugin. Returns an error message, or None on success."""
    sweep_trash()
    try:
        uninstall_plugin_impl(PLUGIN_NAME)
    except PluginNotInstalledError:
        return None
    except PluginInUseError as e:
        return str(e)
    except Exception as e:
        logger.debug("failed to uninstall %s: %s", PLUGIN_NAME, e, exc_info=True)
        return f"could not uninstall the {PLUGIN_NAME} IDA plugin: {e}"
    console.print(f"[green]Uninstalled[/green] IDA plugin: [blue]{PLUGIN_NAME}[/blue]")
    return None


@click.command()
@click.option(
    "--agent",
    "agent_commands",
    multiple=True,
    type=click.Choice(list(_AGENT_DEFINITIONS)),
    help="Only remove IDA MCP from this agent. Repeat for several agents. Default: every agent on PATH.",
)
@click.option(
    "--scope",
    type=click.Choice(["local", "global"]),
    default=None,
    help="Only remove this scope: local (current repository) or global (current user). Default: both.",
)
@click.option("--keep-plugin", is_flag=True, default=False, help="Keep the ida-mcp IDA plugin installed.")
@click.option(
    "--remove-marketplace",
    is_flag=True,
    default=False,
    help="Also remove the HexRaysSA marketplace from Claude Code, Codex, and Copilot.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be removed, and change nothing.")
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask for confirmation.")
def uninstall(
    agent_commands: tuple[str, ...],
    scope: Scope | None,
    keep_plugin: bool,
    remove_marketplace: bool,
    dry_run: bool,
    yes: bool,
) -> None:
    """Remove IDA MCP from Claude, Codex, Copilot, Pi, or Oh My Pi.

    Undoes `hcli mcp install`: removes the agent integrations, then the ida-mcp
    IDA plugin. Only the integration that `hcli mcp install` added is removed: hcli asks each
    agent's own plugin manager to remove the ida-mcp plugin, so MCP servers you
    configured by hand are not touched. Local installs are found from the
    current directory only, so run this from the repository you installed into.
    """
    agents = _find_agents()
    if agent_commands:
        found = {agent.command for agent in agents}
        for command in agent_commands:
            if command not in found:
                stderr_console.print(f"[yellow]{command} is not on PATH; skipping it.[/yellow]")
        agents = [agent for agent in agents if agent.command in agent_commands]

    installations = _find_installations(agents, scope)
    remove_plugin = not keep_plugin and _is_ida_plugin_installed()

    if not installations and not remove_plugin:
        console.print("IDA MCP is not installed for any agent found on PATH. Nothing to do.")
        return

    if not installations:
        console.print("IDA MCP is not installed for any agent found on PATH.")
    else:
        console.print("IDA MCP is installed for:")
        for installation in installations:
            console.print(f"  [blue]{installation.describe()}[/blue]")

    steps = _plan(installations, remove_marketplace, remove_plugin)
    console.print("This will run:")
    for step in steps:
        console.print(f"  [dim]{step}[/dim]")

    if dry_run:
        return
    _confirm(yes)

    # Keep going after a failure, so one broken agent does not strand the rest.
    failures: list[str] = []
    for installation in installations:
        agent = installation.agent
        integration = _integration(agent)
        try:
            _run_checked(agent, integration.uninstall_args(installation.scope))
            if remove_marketplace and integration.marketplace_remove_args is not None:
                _run_checked(agent, integration.marketplace_remove_args(installation.scope))
        except click.ClickException as e:
            failures.append(f"{installation.describe()}: {e.message}")
            continue
        console.print(f"[green]Removed[/green] IDA MCP from [blue]{installation.describe()}[/blue].")

    # The IDA plugin goes last: the agents above may still point at it until then.
    if remove_plugin:
        error = _uninstall_ida_plugin()
        if error is not None:
            failures.append(error)

    if failures:
        raise click.ClickException("uninstall did not complete:\n  " + "\n  ".join(failures))
