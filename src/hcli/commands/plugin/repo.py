"""repository management commands."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import rich.table
import rich_click as click
from rich.markup import escape

from hcli.lib.console import console
from hcli.lib.ida import (
    COMMUNITY_REPO_NAME,
    PLUGIN_REPOSITORY_NAME_RE,
    RESERVED_PLUGIN_REPOSITORIES,
    IDAConfigJson,
    PluginRepositoryConfig,
    get_default_plugin_repository_name,
    get_ida_config,
    get_plugin_repositories,
    set_ida_config,
)
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo

logger = logging.getLogger(__name__)


@click.group()
@click.pass_context
def repo(ctx) -> None:
    """Manage plugin repositories."""


def _save_repositories(config: IDAConfigJson, repos: dict[str, PluginRepositoryConfig], default: str | None) -> None:
    """Write back the config already in hand, rather than re-reading it."""
    config.settings.plugin_repositories = repos
    if default is not None:
        config.settings.default_plugin_repository = default
    set_ida_config(config)


@repo.command(name="list")
@click.pass_context
def list_repos(ctx) -> None:
    """List the configured plugin repositories."""
    config = get_ida_config()
    repos = get_plugin_repositories(config)
    default = get_default_plugin_repository_name(config)

    table = rich.table.Table(show_header=True, box=None)
    table.add_column("name", style="blue")
    table.add_column("url", style="grey69")
    table.add_column("")

    for name in sorted(repos):
        entry = repos[name]
        tags = []
        if name == default:
            tags.append("default")
        if entry.reserved:
            tags.append("reserved")
        table.add_row(name, entry.url, " ".join(tags))

    console.print(table)


@repo.command(name="add")
@click.argument("name")
@click.argument("url")
@click.pass_context
def add_repo(ctx, name: str, url: str) -> None:
    """Add a plugin repository."""
    if name in RESERVED_PLUGIN_REPOSITORIES:
        canonical = RESERVED_PLUGIN_REPOSITORIES[name]
        if url != canonical:
            console.print(f"[red]'{name}' is a reserved repository[/red] and always resolves to {canonical}")
            raise click.Abort()
    if not PLUGIN_REPOSITORY_NAME_RE.match(name):
        console.print(
            f"[red]Invalid repository name '{name}'[/red]: must match {escape(PLUGIN_REPOSITORY_NAME_RE.pattern)}"
        )
        raise click.Abort()
    if urlparse(url).scheme not in ("https", "file"):
        console.print(f"[red]Invalid repository url '{url}'[/red]: must be https:// or file://")
        raise click.Abort()

    config = get_ida_config()
    repos = dict(config.settings.plugin_repositories)
    existing = repos.get(name)
    repos[name] = PluginRepositoryConfig(url=url)
    _save_repositories(config, repos, None)

    verb = "updated" if existing else "added"
    console.print(f"{verb} plugin repository '{name}' -> {url}")


@repo.command(name="remove")
@click.argument("name")
@click.pass_context
def remove_repo(ctx, name: str) -> None:
    """Remove a plugin repository."""
    config = get_ida_config()
    all_repos = get_plugin_repositories(config)
    if name not in all_repos:
        console.print(f"[red]No such plugin repository '{name}'[/red].")
        raise click.Abort()

    remaining = {n: PluginRepositoryConfig(url=r.url) for n, r in all_repos.items() if n != name}
    default = config.settings.default_plugin_repository or COMMUNITY_REPO_NAME
    if default == name:
        new_default = COMMUNITY_REPO_NAME if COMMUNITY_REPO_NAME in remaining else next(iter(remaining), None)
        console.print(
            f"[yellow]'{name}' was the default repository[/yellow]"
            + (f"; unprefixed installs now use '{new_default}'." if new_default else ".")
        )
        _save_repositories(config, remaining, new_default)
    else:
        _save_repositories(config, remaining, default)

    console.print(f"removed plugin repository '{name}'")


@repo.command(name="set-default")
@click.argument("name")
@click.pass_context
def set_default_repo(ctx, name: str) -> None:
    """Set the repository used for references with no repo/ prefix."""
    config = get_ida_config()
    repos = get_plugin_repositories(config)
    if name not in repos:
        known = ", ".join(sorted(repos)) or "none"
        console.print(f"[red]No such plugin repository '{name}'[/red]. Configured: {known}")
        raise click.Abort()

    config.settings.default_plugin_repository = name
    set_ida_config(config)
    console.print(f"default plugin repository is now '{name}'")


@repo.command()
@click.pass_context
def snapshot(ctx) -> None:
    """Create a snapshot of the repository."""
    try:
        repo = JSONFilePluginRepo.from_repo(ctx.obj["plugin_repo"])
        # Use print() instead of console.print() to output raw JSON without ANSI control characters
        print(repo.to_json())
    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
