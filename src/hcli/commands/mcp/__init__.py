from __future__ import annotations

import rich_click as click

from .install import install
from .uninstall import uninstall


@click.group(help="IDA MCP integrations for AI coding agents.")
def mcp() -> None:
    pass


mcp.add_command(install)
mcp.add_command(uninstall)
