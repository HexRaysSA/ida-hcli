from __future__ import annotations

import rich_click as click

from hcli.lib.auth import get_auth_service


@click.command()
def whoami() -> None:
    """Display the currently logged-in user."""
    auth_service = get_auth_service()

    # Initialize auth service
    auth_service.init()

    # Show login information
    auth_service.show_login_info()
