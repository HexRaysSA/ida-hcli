from __future__ import annotations

import rich_click as click

from hcli.lib.auth import get_auth_service
from hcli.lib.console import console


@click.command(name="default")
@click.argument("name", required=False)
def set_default_credentials(name: str | None) -> None:
    """Set or show the default credentials."""
    auth_service = get_auth_service()
    auth_service.init()

    if name is None:
        # Show current default
        default_name = auth_service.get_default_credentials_name()
        if default_name:
            default_source = auth_service.get_current_credentials()
            if default_source:
                console.print(f"[green]Default credentials: {default_name}[/green]")
                console.print(f"Email: {default_source.email}")
                console.print(f"Type: {default_source.type.title()}")
            else:
                console.print(f"[yellow]Default set to '{default_name}' but source not found.[/yellow]")
        else:
            console.print("[yellow]No default credentials set.[/yellow]")

        # Show all available sources
        sources = auth_service.list_credentials()
        if sources:
            console.print(f"\nAvailable sources: {', '.join(s.name for s in sources)}")
        return

    # Set new default
    sources = auth_service.list_credentials()

    if not sources:
        console.print("[yellow]No credentialss found.[/yellow]")
        console.print("Use '[bold]hcli login[/bold]' or '[bold]hcli auth key install[/bold]' to add credentialss.")
        return

    # Check if source exists
    source_names = [s.name for s in sources]
    if name not in source_names:
        console.print(f"[red]Credentials '{name}' not found.[/red]")
        console.print(f"Available sources: {', '.join(source_names)}")
        return

    # Check if already default
    current_default = auth_service.get_default_credentials_name()
    if name == current_default:
        console.print(f"[yellow]'{name}' is already the default credentials.[/yellow]")
        return

    # Set as default
    if auth_service.set_default_credentials(name):
        console.print(f"[green]Set '{name}' as the default credentials.[/green]")

        # Show the updated auth info
        console.print()
        auth_service.show_login_info()
    else:
        console.print(f"[red]Failed to set '{name}' as default credentials.[/red]")
