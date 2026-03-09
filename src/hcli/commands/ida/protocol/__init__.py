from __future__ import annotations

import rich_click as click


@click.group()
def protocol() -> None:
    """Manage ida:// protocol handlers."""


from .register import register
from .unregister import unregister

protocol.add_command(register)
protocol.add_command(unregister)
