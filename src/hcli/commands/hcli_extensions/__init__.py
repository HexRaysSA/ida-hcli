from __future__ import annotations

import rich_click as click

from hcli.commands.hcli_extensions.create import create
from hcli.commands.hcli_extensions.list import list_extensions


@click.group(name="hcli-extensions", help="Manage hcli extensions.")
def hcli_extensions() -> None:
    pass


hcli_extensions.add_command(list_extensions, name="list")
hcli_extensions.add_command(create)
