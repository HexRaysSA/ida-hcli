from __future__ import annotations

import rich_click as click


@click.group()
def source() -> None:
    """Manage knowledge sources."""


from .add import add
from .list import list_sources
from .remove import remove

source.add_command(add)
source.add_command(remove)
source.add_command(list_sources, name="list")
