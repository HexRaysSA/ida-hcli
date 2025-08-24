from __future__ import annotations

import rich_click as click

from .enable import disable_plugin, enable_plugin
from .install import install_plugin
from .list import list_plugins
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
    ctx.obj["token"] = token
    ctx.obj["path"] = path


plugin.add_command(get_plugin_status, name="status")
plugin.add_command(list_plugins, name="list")
plugin.add_command(search_plugins, name="search")
plugin.add_command(install_plugin, name="install")
plugin.add_command(enable_plugin, name="enable")
plugin.add_command(disable_plugin, name="disable")
plugin.add_command(upgrade_plugin, name="upgrade")
plugin.add_command(uninstall_plugin, name="uninstall")
