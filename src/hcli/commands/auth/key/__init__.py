from __future__ import annotations

import rich_click as click

from hcli.commands.auth.key.create import create
from hcli.commands.auth.key.install import install_key
from hcli.commands.auth.key.list import list_keys
from hcli.commands.auth.key.revoke import revoke


@click.group()
def key() -> None:
    """API key management."""
    pass


key.add_command(create)
key.add_command(list_keys, name="list")
key.add_command(revoke)
key.add_command(install_key)
