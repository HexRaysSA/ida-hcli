from __future__ import annotations

import rich_click as click

from .enable import disable_plugin, enable_plugin
from .install import install_plugin
from .list import list_plugins
from .search import search_plugins
from .uninstall import uninstall_plugin
from .upgrade import upgrade_plugin


@click.group()
@click.pass_context
def plugin(_ctx) -> None:
    """Manage IDA Pro plugins."""
    pass


# TODO: maybe make this `status` rather than list
# since list might operate against the server or current state
plugin.add_command(list_plugins, name="list")
plugin.add_command(search_plugins, name="search")
plugin.add_command(install_plugin, name="install")
plugin.add_command(enable_plugin, name="enable")
plugin.add_command(disable_plugin, name="disable")
plugin.add_command(upgrade_plugin, name="upgrade")
plugin.add_command(uninstall_plugin, name="uninstall")
