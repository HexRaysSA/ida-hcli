from __future__ import annotations

import rich_click as click


@click.group()
def ke() -> None:
    """Knowledge Engine commands."""


from .ida import ida
from .open import open_url
from .setup import install, setup
from .source import source

ke.add_command(ida)
ke.add_command(install)
ke.add_command(open_url, name="open")
ke.add_command(source)
ke.add_command(setup)
