from __future__ import annotations

import rich_click as click


@click.group()
def cache() -> None:
    """Manage hcli's cache of downloaded files."""


from .clear import clear_cache

cache.add_command(clear_cache, name="clear")
