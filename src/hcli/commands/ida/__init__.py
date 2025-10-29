from __future__ import annotations

import rich_click as click


@click.group()
def ida() -> None:
    """Manage IDA installations."""
    pass


from .accept_eula import accept_eula_cmd  # noqa: E402
from .install import install  # noqa: E402
from .set_default import set_default_ida  # noqa: E402

ida.add_command(accept_eula_cmd)
ida.add_command(install)
ida.add_command(set_default_ida)
