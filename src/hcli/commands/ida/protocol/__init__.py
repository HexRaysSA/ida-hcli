from __future__ import annotations

import rich_click as click


@click.group()
def protocol() -> None:
    """Manage ida:// protocol handlers."""
    pass


from .register import register  # noqa: E402

protocol.add_command(register)
