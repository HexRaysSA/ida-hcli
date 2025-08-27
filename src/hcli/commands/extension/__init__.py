from __future__ import annotations

import rich_click as click

from hcli.commands.extension.create import create
from hcli.commands.extension.list import list_extensions


@click.group(help="Manage hcli extensions.")
def extension() -> None:
    pass


extension.add_command(list_extensions, name="list")
extension.add_command(create)
