from __future__ import annotations

import os
from pathlib import Path

import rich_click as click

import hcli.lib.ida.plugin.repo.github
from hcli.lib.console import console

from .install import install_plugin
from .search import search_plugins
from .status import get_plugin_status
from .uninstall import uninstall_plugin
from .upgrade import upgrade_plugin


@click.group()
@click.option("--token", help="GitHub token for authentication")
@click.option("--path", help="Path to file system repository of plugins")
@click.pass_context
def plugin(ctx, token, path) -> None:
    """Manage IDA Pro plugins."""
    ctx.ensure_object(dict)

    plugin_repo: hcli.lib.ida.plugin.repo.BasePluginRepo
    if token:
        plugin_repo = hcli.lib.ida.plugin.repo.github.GithubPluginRepo(ctx.obj["token"])
    elif path:
        plugin_repo = hcli.lib.ida.plugin.repo.fs.FileSystemPluginRepo(Path(ctx.obj["path"]))
    elif "GITHUB_TOKEN" in os.environ:
        plugin_repo = hcli.lib.ida.plugin.repo.github.GithubPluginRepo(os.environ["GITHUB_TOKEN"])
    else:
        console.print("[red]GitHub token required[/red]. Set GITHUB_TOKEN environment variable or provide --token")
        raise click.Abort()

    ctx.obj["plugin_repo"] = plugin_repo


plugin.add_command(get_plugin_status, name="status")
plugin.add_command(search_plugins, name="search")
plugin.add_command(install_plugin, name="install")
plugin.add_command(upgrade_plugin, name="upgrade")
plugin.add_command(uninstall_plugin, name="uninstall")
