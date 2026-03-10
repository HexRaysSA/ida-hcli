from __future__ import annotations

import rich_click as click


@click.group()
def tool() -> None:
    """Manage IDA-related utility tools."""


from .install import install_tool
from .list import list_tools
from .remove import remove_tool
from .search import search_tools

tool.add_command(install_tool, name="install")
tool.add_command(list_tools, name="list")
tool.add_command(remove_tool, name="remove")
tool.add_command(search_tools, name="search")
