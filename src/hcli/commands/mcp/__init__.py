from __future__ import annotations

import rich_click as click

from .install import install


@click.group(help="Install and manage IDA MCP integrations.")
def mcp() -> None:
    pass


mcp.add_command(install)
