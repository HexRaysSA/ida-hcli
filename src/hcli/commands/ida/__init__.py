from __future__ import annotations

import rich_click as click


@click.group()
def ida() -> None:
    """Manage IDA installations."""
    pass


from .add import add  # noqa: E402
from .install import install  # noqa: E402
from .list import list_instances  # noqa: E402
from .open import open_link  # noqa: E402
from .protocol import protocol  # noqa: E402
from .remove import remove  # noqa: E402
from .search_path import search_path  # noqa: E402
from .switch import switch  # noqa: E402

ida.add_command(add)
ida.add_command(install)
ida.add_command(list_instances, name="list")
ida.add_command(open_link)
ida.add_command(protocol)
ida.add_command(remove)
ida.add_command(search_path)
ida.add_command(switch)
