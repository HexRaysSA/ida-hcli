"""repository management commands."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo
from hcli.lib.ida.plugin.repo.github import (
    find_github_repos_with_plugins,
    read_repos_from_file,
    set_candidate_github_repos_cache,
)

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


@repo.command()
@click.option(
    "--with-repos-list",
    type=click.Path(exists=True, path_type=Path),
    help="Path to file containing additional GitHub repositories (format: owner/repo, one per line)",
)
@click.pass_context
def sync(ctx, with_repos_list: Path | None) -> None:
    """Sync the GitHub plugin repository cache with discovered repositories."""
    try:
        # Check if we have a GitHub token
        try:
            token = os.environ["GITHUB_TOKEN"]
        except KeyError:
            console.print("[red]GitHub token required[/red]. Set GITHUB_TOKEN environment variable.")
            raise click.Abort()

        # Read additional repos from file if provided
        additional_repos = None
        if with_repos_list:
            try:
                additional_repos = read_repos_from_file(with_repos_list)
                console.print(
                    f"[green]Loaded {len(additional_repos)} additional repositories from {with_repos_list}[/green]"
                )
            except (FileNotFoundError, ValueError) as e:
                console.print(f"[red]Error reading repositories file[/red]: {e}")
                raise click.Abort()

        # Find GitHub repositories with plugins
        console.print("[blue]Searching for GitHub repositories with IDA plugins...[/blue]")
        repos = find_github_repos_with_plugins(token, additional_repos)

        # Update the cache
        set_candidate_github_repos_cache(repos)

        discovered_count = len(repos) - (len(additional_repos) if additional_repos else 0)
        total_count = len(repos)

        if additional_repos:
            console.print(
                f"[green]Sync complete![/green] Found {discovered_count} repositories via search + {len(additional_repos)} from file = {total_count} total repositories cached."
            )
        else:
            console.print(f"[green]Sync complete![/green] Found {total_count} repositories cached.")

    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
