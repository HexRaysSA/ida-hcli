from __future__ import annotations

import rich_click as click


@click.group()
def license() -> None:
    """Manage IDA licenses."""


from .get import get_license
from .install import install_license
from .list import list_licenses

license.add_command(list_licenses, name="list")
license.add_command(get_license, name="get")
license.add_command(install_license, name="install")
