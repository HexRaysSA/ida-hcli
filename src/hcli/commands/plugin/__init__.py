from __future__ import annotations

import os
from pathlib import Path

import rich_click as click

import hcli.lib.ida.plugin.repo
import hcli.lib.ida.plugin.repo.file
import hcli.lib.ida.plugin.repo.fs
import hcli.lib.ida.plugin.repo.github
from hcli.lib.console import console
from hcli.lib.ida import get_ida_config

from .install import install_plugin
from .lint import lint_plugin_directory
from .repo import repo
from .search import search_plugins
from .status import get_plugin_status
from .uninstall import uninstall_plugin
from .upgrade import upgrade_plugin


@click.group()
@click.option(
    "--repo", help="'github', or path to directory containing plugins, or path to JSON file, or URL to JSON file"
)
@click.option("--with-repos-list", help="path to file containing known GitHub repositories")
@click.pass_context
def plugin(ctx, repo: str | None, with_repos_list: str | None) -> None:
    """Manage IDA Pro plugins."""
    # TODO: cleanup list and anything else touching github
    ctx.ensure_object(dict)

    plugin_repo: hcli.lib.ida.plugin.repo.BasePluginRepo
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
            if not repos_list_path.exists():
                console.print("[red]repos list file doesn't exist[/red].")
                raise click.Abort()

            extra_repos = [repo.strip() for repo in repos_list_path.read_text().split("\n") if repo.strip()]

        plugin_repo = hcli.lib.ida.plugin.repo.github.GithubPluginRepo(token, extra_repos=extra_repos)

    else:
        path = Path(repo)
        if not path.exists():
            console.print("[red]Repository doesn't exist[/red]. Provide `--repo github` or `--repo /path/to/plugins/`.")
            raise click.Abort()

        if path.is_dir():
            plugin_repo = hcli.lib.ida.plugin.repo.fs.FileSystemPluginRepo(path)
        else:
            plugin_repo = hcli.lib.ida.plugin.repo.file.JSONFilePluginRepo.from_file(path)

    ctx.obj["plugin_repo"] = plugin_repo


plugin.add_command(get_plugin_status, name="status")
plugin.add_command(search_plugins, name="search")
plugin.add_command(install_plugin, name="install")
plugin.add_command(lint_plugin_directory, name="lint")
plugin.add_command(upgrade_plugin, name="upgrade")
plugin.add_command(uninstall_plugin, name="uninstall")
plugin.add_command(repo, name="repo")
