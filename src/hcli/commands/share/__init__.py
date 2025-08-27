from __future__ import annotations

import rich_click as click


@click.group()
def share() -> None:
    """Share files with Hex-Rays."""
    pass


from .delete import delete  # noqa: E402
from .get import get  # noqa: E402
from .list import list_shares  # noqa: E402
from .put import put  # noqa: E402

share.add_command(get)
share.add_command(put)
share.add_command(delete)
share.add_command(list_shares, name="list")
