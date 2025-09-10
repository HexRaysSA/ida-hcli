"""repository management commands."""

from __future__ import annotations

import logging

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo

logger = logging.getLogger(__name__)


@click.group(hidden=True)
@click.pass_context
def repo(ctx) -> None:
    """Manage plugin repositories."""
    pass


@repo.command()
@click.pass_context
def snapshot(ctx) -> None:
    """Create a snapshot of the repository."""
    try:
        repo = JSONFilePluginRepo.from_repo(ctx.obj["plugin_repo"])
        print(repo.to_json())
    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
