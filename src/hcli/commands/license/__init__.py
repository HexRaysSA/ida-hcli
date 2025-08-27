from __future__ import annotations

import rich_click as click


@click.group()
def license() -> None:
    """Manage IDA licenses."""
    pass


from .get import get_license  # noqa: E402
from .install import install_license  # noqa: E402
from .list import list_licenses  # noqa: E402

license.add_command(list_licenses, name="list")
license.add_command(get_license, name="get")
license.add_command(install_license, name="install")
