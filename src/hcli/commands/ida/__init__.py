from __future__ import annotations

import rich_click as click


@click.group()
def ida() -> None:
    """Manage IDA installations."""


from .accept_eula import accept_eula_command
from .install import install
from .set_default import set_default_ida

ida.add_command(accept_eula_command)
ida.add_command(install)
ida.add_command(set_default_ida)
