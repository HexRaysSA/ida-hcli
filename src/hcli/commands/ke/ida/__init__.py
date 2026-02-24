from __future__ import annotations

import rich_click as click


@click.group()
def ida() -> None:
    """Manage IDA Pro instances."""


from .add import add
from .list import list_instances
from .remove import remove
from .switch import switch

ida.add_command(add)
ida.add_command(remove)
ida.add_command(list_instances, name="list")
ida.add_command(switch)
