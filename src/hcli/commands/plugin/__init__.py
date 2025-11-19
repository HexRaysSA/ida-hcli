from __future__ import annotations

import os
from pathlib import Path

import requests
import rich_click as click

import hcli.lib.ida.plugin.repo
import hcli.lib.ida.plugin.repo.file
import hcli.lib.ida.plugin.repo.fs
import hcli.lib.ida.plugin.repo.github
from hcli.lib.console import console
from hcli.lib.ida import get_ida_config

from .config import config
from .install import install_plugin
from .lint import lint_plugin_directory
from .repo import repo
from .search import search_plugins
from .status import get_plugin_status
from .uninstall import uninstall_plugin
from .upgrade import upgrade_plugin


def read_repos_file(path: Path) -> list[str]:
    if not path.exists():
        raise ValueError(f"file doesn't exist: {path}")

    repos = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        if line.startswith("#"):
            continue

        repos.append(line.strip())

    return repos


@click.group()
@click.option(
    "--repo",
    help="'github', or path to directory containing plugins, or path to JSON file, or URL to JSON file",
    hidden=True,
)
@click.option("--with-repos-list", help="path to file containing known GitHub repositories", hidden=True)
@click.option("--with-ignored-repos-list", help="path to file containing ignored GitHub repositories", hidden=True)
@click.pass_context
def plugin(ctx, repo: str | None, with_repos_list: str | None, with_ignored_repos_list: str | None) -> None:
    """Manage IDA Pro plugins."""
    # TODO: cleanup list and anything else touching github
    ctx.ensure_object(dict)

    plugin_repo: hcli.lib.ida.plugin.repo.BasePluginRepo
    try:
        if repo is None:
            config = get_ida_config()

            url = config.settings.plugin_repository.url
            if not url:
                console.print(
                    "[red]Missing plugin repository URL[/red]. Provide this in ida-config.json (.Settings.plugin-repository.url)"
                )
                raise click.Abort()

            plugin_repo = hcli.lib.ida.plugin.repo.file.JSONFilePluginRepo.from_url(url)

        elif repo == "github":
            try:
                token = os.environ["GITHUB_TOKEN"]
            except KeyError:
                console.print("[red]GitHub token required[/red]. Set GITHUB_TOKEN environment variable.")
                raise click.Abort()

            extra_repos = []
            if with_repos_list is not None:
                repos_list_path = Path(with_repos_list)
                try:
                    extra_repos = read_repos_file(repos_list_path)
                except ValueError as e:
                    console.print(f"[red]failed to read repos list file[/red]: {str(e)}.")
                    raise click.Abort()

            ignored_repos = []
            if with_ignored_repos_list is not None:
                ignored_repos_list_path = Path(with_ignored_repos_list)
                try:
                    ignored_repos = read_repos_file(ignored_repos_list_path)
                except ValueError as e:
                    console.print(f"[red]failed to read ignored repos list file[/red]: {str(e)}.")
                    raise click.Abort()

            plugin_repo = hcli.lib.ida.plugin.repo.github.GithubPluginRepo(
                token, extra_repos=extra_repos, ignored_repos=ignored_repos
            )

        else:
            path = Path(repo)
            if not path.exists():
                console.print(
                    "[red]Repository doesn't exist[/red]. Provide `--repo github` or `--repo /path/to/plugins/`."
                )
                raise click.Abort()

            if path.is_dir():
                plugin_repo = hcli.lib.ida.plugin.repo.fs.FileSystemPluginRepo(path)
            else:
                plugin_repo = hcli.lib.ida.plugin.repo.file.JSONFilePluginRepo.from_file(path)

    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        if repo == "github":
            console.print("[red]Cannot connect to GitHub - network unavailable.[/red]")
        elif repo is None:
            config = get_ida_config()
            url = config.settings.plugin_repository.url
            console.print(f"[red]Cannot connect to plugin repository at {url} - network unavailable.[/red]")
        else:
            console.print("[red]Cannot connect to plugin repository - network unavailable.[/red]")
        console.print("Please check your internet connection.")
        raise click.Abort()

    ctx.obj["plugin_repo"] = plugin_repo


plugin.add_command(get_plugin_status, name="status")
plugin.add_command(search_plugins, name="search")
plugin.add_command(install_plugin, name="install")
plugin.add_command(lint_plugin_directory, name="lint")
plugin.add_command(upgrade_plugin, name="upgrade")
plugin.add_command(uninstall_plugin, name="uninstall")
plugin.add_command(repo, name="repo")
plugin.add_command(config, name="config")
