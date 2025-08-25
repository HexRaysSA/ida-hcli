from __future__ import annotations

import os
from pathlib import Path

import rich_click as click

import hcli.lib.ida.plugin.repo
import hcli.lib.ida.plugin.repo.fs
import hcli.lib.ida.plugin.repo.github
from hcli.lib.console import console

from .install import install_plugin
from .search import search_plugins
from .status import get_plugin_status
from .uninstall import uninstall_plugin
from .upgrade import upgrade_plugin


@click.group()
@click.option("--repo", help="'github' or path to directory containing plugins")
@click.pass_context
def plugin(ctx, repo: str | None) -> None:
    """Manage IDA Pro plugins."""
    # TODO: cleanup list and anything else touching github
    ctx.ensure_object(dict)

    plugin_repo: hcli.lib.ida.plugin.repo.BasePluginRepo
    # TODO: plugins.hex-rays.com repo, and use this as default
    if repo is None or repo == "github":
        try:
            token = os.environ["GITHUB_TOKEN"]
        except KeyError:
            console.print("[red]GitHub token required[/red]. Set GITHUB_TOKEN environment variable.")
            raise click.Abort()
        plugin_repo = hcli.lib.ida.plugin.repo.github.GithubPluginRepo(token)

    else:
        path = Path(repo)
        if not path.exists():
            console.print("[red]Repository doesn't exist[/red]. Provide `--repo github` or `--repo /path/to/plugins/`.")
            raise click.Abort()

        if not path.is_dir():
            console.print(
                "[red]Repository not a directory[/red]. Provide `--repo github` or `--repo /path/to/plugins/`."
            )
            raise click.Abort()

        # TODO: simple snapshot repository as JSON file

        plugin_repo = hcli.lib.ida.plugin.repo.fs.FileSystemPluginRepo(path)

    ctx.obj["plugin_repo"] = plugin_repo


plugin.add_command(get_plugin_status, name="status")
plugin.add_command(search_plugins, name="search")
plugin.add_command(install_plugin, name="install")
plugin.add_command(upgrade_plugin, name="upgrade")
plugin.add_command(uninstall_plugin, name="uninstall")
