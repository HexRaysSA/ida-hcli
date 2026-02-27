from __future__ import annotations

import rich_click as click


@click.group()
def ida() -> None:
    """Manage IDA installations."""


from .accept_eula import accept_eula_command
from .add import add
from .install import install
from .list import list_instances
from .open import open_ida_link
from .protocol import protocol
from .remove import remove
from .set_default import set_default_ida
from .source import source
from .switch import switch

ida.add_command(accept_eula_command)
ida.add_command(add)
ida.add_command(install)
ida.add_command(list_instances, name="list")
ida.add_command(open_ida_link)
ida.add_command(protocol)
ida.add_command(remove)
ida.add_command(set_default_ida)
ida.add_command(source)
ida.add_command(switch)
