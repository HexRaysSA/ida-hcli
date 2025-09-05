from __future__ import annotations

import rich_click as click


@click.group()
def ida() -> None:
    """Manage IDA Pro instances."""
    pass


from .add import add  # noqa: E402
from .list import list_instances  # noqa: E402
from .remove import remove  # noqa: E402
from .switch import switch  # noqa: E402

ida.add_command(add)
ida.add_command(remove)
ida.add_command(list_instances, name="list")
ida.add_command(switch)
