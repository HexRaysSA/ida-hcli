"""Plugin list command."""

from __future__ import annotations

import logging
import os

import rich_click as click

import hcli.lib.ida.plugin.repo.github
from hcli.lib.commands import async_command
from hcli.lib.console import console

logger = logging.getLogger(__name__)


@click.command()
@async_command
async def list_plugins() -> None:
    try:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            console.print("[red]GitHub token required[/red]. Set GITHUB_TOKEN environment variable or provide --token")
            return

        assert token is not None
        assert isinstance(token, str)

        plugin_repo = hcli.lib.ida.plugin.repo.github.GithubPluginRepo(token)
        for plugin in sorted(plugin_repo.get_plugins(), key=lambda p: p.name):
            console.print(f"[green]{plugin.name}[/green]")

            for version in plugin.versions:
                console.print(f"  [yellow]{version.version}:[/yellow] {version.url}")

    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
