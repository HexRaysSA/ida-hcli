from __future__ import annotations

import rich_click as click


@click.group(name="search-path")
def search_path() -> None:
    """Manage IDB search paths for auto-launch."""
    pass


from .add import add  # noqa: E402
from .list import list_paths  # noqa: E402
from .remove import remove  # noqa: E402

search_path.add_command(add)
search_path.add_command(list_paths, name="list")
search_path.add_command(remove)
