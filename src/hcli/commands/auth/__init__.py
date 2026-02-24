from __future__ import annotations

import rich_click as click


@click.group()
def auth() -> None:
    """Manage hcli api keys."""


# Subcommands
from .default import set_default_credentials
from .key import key
from .list import list_credentials
from .switch import switch_credentials

auth.add_command(key)
auth.add_command(list_credentials)
auth.add_command(switch_credentials)
auth.add_command(set_default_credentials)
