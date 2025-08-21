"""Plugin list command."""

from __future__ import annotations

import logging
import os

import rich_click as click

import hcli.lib.ida.plugin.repo.github
from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.ida.plugin import ALL_PLATFORMS

logger = logging.getLogger(__name__)


@click.command()
@click.pass_context
@async_command
async def list_plugins(ctx) -> None:
    try:
        # Use token from context if provided, otherwise fall back to environment variable
        token = ctx.obj.get("token") if ctx.obj else None
        if not token:
            token = os.getenv("GITHUB_TOKEN")

        if not token:
            console.print("[red]GitHub token required[/red]. Set GITHUB_TOKEN environment variable or provide --token")
            return

        assert token is not None
        assert isinstance(token, str)

        plugin_repo = hcli.lib.ida.plugin.repo.github.GithubPluginRepo(token)

        plugins = plugin_repo.get_plugins()

        for plugin in sorted(plugins, key=lambda p: p.name):
            console.print(f"[blue]{plugin.name}[/blue]")

            for version, locations in plugin.locations_by_version.items():
                console.print(f"  {version}:")
                for location in locations:
                    ida_versions_str = location.ida_versions if location.ida_versions != ">=0" else "all"
                    locations_str = ", ".join(location.platforms) if location.platforms != ALL_PLATFORMS else "all"
                    console.print(
                        f"    IDA: [yellow]{ida_versions_str}[/yellow], platforms: [yellow]{locations_str}[/yellow]"
                    )
                    console.print(f"    {location.url}")
                    console.print("")

    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
