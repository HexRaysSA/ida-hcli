from __future__ import annotations

import rich_click as click

from .install import install


@click.group(help="IDA MCP integrations for AI coding agents.")
def mcp() -> None:
    pass


mcp.add_command(install)
