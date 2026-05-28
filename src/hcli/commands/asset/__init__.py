from __future__ import annotations

import rich_click as click


@click.group(hidden=True)
def asset() -> None:
    """Low-level asset bucket commands."""


from .delete import delete
from .put import put

asset.add_command(put)
asset.add_command(delete)
