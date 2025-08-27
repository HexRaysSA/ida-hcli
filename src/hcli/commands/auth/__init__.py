from __future__ import annotations

import rich_click as click


@click.group()
def auth() -> None:
    """Manage hcli api keys."""
    pass


# Subcommands
from .default import set_default_credentials  # noqa: E402
from .key import key  # noqa: E402
from .list import list_credentials  # noqa: E402
from .switch import switch_credentials  # noqa: E402

auth.add_command(key)
auth.add_command(list_credentials)
auth.add_command(switch_credentials)
auth.add_command(set_default_credentials)
