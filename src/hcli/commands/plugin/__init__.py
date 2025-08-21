from __future__ import annotations

import rich_click as click


@click.group()
@click.option('--token', help='GitHub token for authentication')
@click.pass_context
def plugin(ctx, token) -> None:
    """Manage IDA Pro plugins."""
    ctx.ensure_object(dict)
    ctx.obj['token'] = token


from .list import list_plugins
from .search import search_plugins
from .install import install_plugin
from .enable import enable_plugin, disable_plugin
from .upgrade import upgrade_plugin
from .uninstall import uninstall_plugin

# TODO: maybe make this `status` rather than list
# since list might operate against the server or current state
plugin.add_command(list_plugins, name="list")
plugin.add_command(search_plugins, name="search")
plugin.add_command(install_plugin, name="install")
plugin.add_command(enable_plugin, name="enable")
plugin.add_command(disable_plugin, name="disable")
plugin.add_command(upgrade_plugin, name="upgrade")
plugin.add_command(uninstall_plugin, name="uninstall")
