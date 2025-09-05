from __future__ import annotations

import rich_click as click


@click.group()
def source() -> None:
    """Manage knowledge sources."""
    pass


from .add import add  # noqa: E402
from .list import list_sources  # noqa: E402
from .remove import remove  # noqa: E402

source.add_command(add)
source.add_command(remove)
source.add_command(list_sources, name="list")
