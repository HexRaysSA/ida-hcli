from __future__ import annotations

import rich_click as click


@click.group()
def ke() -> None:
    """Knowledge Engine commands."""
    pass


from .setup import setup  # noqa: E402
from .source import source  # noqa: E402

ke.add_command(source)
ke.add_command(setup)
