from __future__ import annotations

import rich_click as click


@click.group()
def ke() -> None:
    """Knowledge Engine commands."""
    pass


from .ida import ida  # noqa: E402
from .open import open_url  # noqa: E402
from .setup import install, setup  # noqa: E402
from .source import source  # noqa: E402

ke.add_command(ida)
ke.add_command(install)
ke.add_command(open_url, name="open")
ke.add_command(source)
ke.add_command(setup)
