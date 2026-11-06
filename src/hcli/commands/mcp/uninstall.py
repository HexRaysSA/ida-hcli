"""Remove the IDA MCP plugin and agent integrations."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

import rich_click as click

from hcli.commands.mcp._agents import (
    AGENT_COMMANDS,
    PLUGIN_NAME,
    Agent,
    Scope,
    find_agents,
    get_installed_scopes,
    get_scope_description,
    get_uninstall_args,
    render_command,
    uninstall_agent,
)
from hcli.lib.console import console
from hcli.lib.ida.plugin.install import is_plugin_installed, sweep_trash, uninstall_plugin

logger = logging.getLogger(__name__)

MANUAL_CONFIGURATION_NOTE = (
    "[dim]If you added the IDA MCP server to an agent's MCP configuration by hand, remove that entry by hand.[/dim]"
)


@dataclass(frozen=True)
class Integration:
    agent: Agent
    scope: Scope

    def describe(self) -> str:
        return f"{self.agent.name} {get_scope_description(self.scope)}"


@dataclass(frozen=True)
class UninstallPlan:
    targets: list[Integration]
    kept: list[Integration]
    remove_ida_plugin: bool

    @classmethod
    def from_installed(
        cls,
        installed: list[Integration],
        *,
        has_ida_plugin: bool,
        agents: tuple[str, ...] = (),
        scope: Scope | None = None,
        keep_plugin: bool = False,
    ) -> UninstallPlan:
        """Select the integrations to remove, and keep the IDA plugin while a detected integration stays."""

        def is_selected(integration: Integration) -> bool:
            return (not agents or integration.agent.command in agents) and (scope is None or integration.scope == scope)

        targets = [integration for integration in installed if is_selected(integration)]
        kept = [integration for integration in installed if not is_selected(integration)]
        return cls(targets=targets, kept=kept, remove_ida_plugin=has_ida_plugin and not keep_plugin and not kept)


def _output_plan(plan: UninstallPlan, *, dry_run: bool) -> None:
    console.print("Remove IDA MCP from:")
    for integration in plan.targets:
        console.print(f"  {integration.describe()}")
        if dry_run:
            args = get_uninstall_args(integration.agent, integration.scope)
            console.print(f"    [dim]{render_command(integration.agent, args)}[/dim]")
    if plan.remove_ida_plugin:
        console.print(f"  IDA plugin {PLUGIN_NAME}")


def _remove(plan: UninstallPlan) -> list[str]:
    """Remove the planned integrations, and then the IDA plugin when every agent removal succeeded.

    Returns:
        A description of each removal that failed.
    """
    failures: list[str] = []
    # Remove the agent integrations first, so that no agent is left configured for a removed IDA plugin.
    for integration in plan.targets:
        try:
            uninstall_agent(integration.agent, integration.scope)
        except click.ClickException as e:
            logger.debug("failed to remove IDA MCP from %s: %s", integration.describe(), e.message)
            failures.append(f"{integration.describe()}: {e.message}")
        else:
            console.print(f"[green]Removed[/green] IDA MCP from {integration.describe()}")

    if not plan.remove_ida_plugin:
        return failures
    if failures:
        console.print(
            f"The IDA plugin {PLUGIN_NAME} stays installed, because IDA MCP could not be removed from an agent."
        )
        return failures

    try:
        uninstall_plugin(PLUGIN_NAME)
    except Exception as e:
        logger.debug("failed to uninstall the IDA plugin %s", PLUGIN_NAME, exc_info=True)
        failures.append(f"IDA plugin {PLUGIN_NAME}: {e}")
    else:
        console.print(f"[green]Uninstalled[/green] plugin: [blue]{PLUGIN_NAME}[/blue]")
    return failures


@click.command()
@click.option(
    "--agent",
    "agents",
    type=click.Choice(AGENT_COMMANDS, case_sensitive=False),
    multiple=True,
    help="Remove IDA MCP only from this agent. Repeat to select more agents.",
)
@click.option(
    "--scope",
    type=click.Choice(["local", "global"], case_sensitive=False),
    default=None,
    help="Remove IDA MCP only from this scope.",
)
@click.option("--keep-plugin", is_flag=True, default=False, help="Do not remove the IDA plugin.")
@click.option("--dry-run", is_flag=True, default=False, help="Show what to remove, and remove nothing.")
@click.option("--yes", "-y", is_flag=True, default=False, help="Remove without a confirmation prompt.")
def uninstall(agents: tuple[str, ...], scope: Scope | None, keep_plugin: bool, dry_run: bool, yes: bool) -> None:
    """Remove IDA MCP from IDA and from the agents on PATH that have it.

    The local scope of an agent is the repository in the current directory.
    Run this command in each repository where you installed IDA MCP locally.

    The IDA plugin is removed only when no detected agent integration stays installed.
    """
    sweep_trash()
    failures: list[str] = []
    installed = [Integration(agent, found) for agent in find_agents() for found in get_installed_scopes(agent)]
    try:
        has_ida_plugin = is_plugin_installed(PLUGIN_NAME)
    except Exception as e:
        logger.debug("could not find the IDA plugin %s", PLUGIN_NAME, exc_info=True)
        console.print(f"[yellow]Could not find the IDA plugin {PLUGIN_NAME}: {e}[/yellow]")
        failures.append(f"IDA plugin {PLUGIN_NAME}: {e}")
        has_ida_plugin = False

    plan = UninstallPlan.from_installed(
        installed, has_ida_plugin=has_ida_plugin, agents=agents, scope=scope, keep_plugin=keep_plugin
    )
    has_work = bool(plan.targets) or plan.remove_ida_plugin

    if has_work:
        _output_plan(plan, dry_run=dry_run)
    elif installed or has_ida_plugin:
        console.print("IDA MCP is not installed for the selected agents and scopes.")
    elif not failures:
        console.print("IDA MCP is not installed for IDA or for an agent on PATH.")

    if plan.kept and has_ida_plugin and not keep_plugin:
        console.print(f"The IDA plugin {PLUGIN_NAME} stays installed, because IDA MCP stays installed for:")
        for integration in plan.kept:
            console.print(f"  {integration.describe()}")

    if dry_run:
        console.print("Dry run: nothing was removed.")
    elif has_work:
        if not yes:
            if not sys.stdin.isatty():
                raise click.ClickException("confirmation required: run with --yes to remove without a prompt")
            if not click.confirm("Remove IDA MCP from all of these?", default=False):
                raise click.Abort()
        failures.extend(_remove(plan))

    console.print(MANUAL_CONFIGURATION_NOTE)

    if failures:
        raise click.ClickException("could not remove IDA MCP from:\n  " + "\n  ".join(failures))
