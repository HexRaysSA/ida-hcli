"""Plugin list command."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import rich_click as click

import hcli.lib.ida.plugin.repo.fs
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
        if ctx.obj.get("token"):
            plugin_repo = hcli.lib.ida.plugin.repo.github.GithubPluginRepo(ctx.obj["token"])
        elif ctx.obj.get("path"):
            plugin_repo = hcli.lib.ida.plugin.repo.fs.FileSystemPluginRepo(Path(ctx.obj["path"]))
        elif "GITHUB_TOKEN" in os.environ:
            plugin_repo = hcli.lib.ida.plugin.repo.github.GithubPluginRepo(os.environ["GITHUB_TOKEN"])
        else:
            console.print("[red]GitHub token required[/red]. Set GITHUB_TOKEN environment variable or provide --token")
            return

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

        if not plugins:
            console.print("[grey69]No plugins found[/grey69]")

    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
