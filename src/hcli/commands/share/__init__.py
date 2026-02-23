from __future__ import annotations

import rich_click as click


@click.group()
def share() -> None:
    """Share files with Hex-Rays."""


from .delete import delete
from .get import get
from .list import list_shares
from .put import put

share.add_command(get)
share.add_command(put)
share.add_command(delete)
share.add_command(list_shares, name="list")
