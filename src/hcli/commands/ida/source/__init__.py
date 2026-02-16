from __future__ import annotations

import rich_click as click


@click.group()
def source() -> None:
    """Manage named sources for IDB file lookup."""
    pass


from .add import add
from .list import list_sources
from .remove import remove

source.add_command(add)
source.add_command(list_sources, name="list")
source.add_command(remove)
