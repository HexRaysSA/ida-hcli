from __future__ import annotations

import questionary
import rich_click as click

from hcli.commands.common import safe_ask_async
from hcli.lib.auth import get_auth_service
from hcli.lib.commands import async_command
from hcli.lib.console import console


@click.command(name="install")
@click.option("-k", "--key", help="API key to install")
@click.option("-n", "--name", help="Custom name for the credentials")
@click.option("--key-name", help="Name for the API key")
@click.option("--set-default", is_flag=True, help="Set as default credentials")
@async_command
async def install_key(key: str | None, name: str | None, key_name: str | None, set_default: bool) -> None:
    """Install an API key as a new credentials."""
    auth_service = get_auth_service()
    auth_service.init()

    # Show current status
    current_source = auth_service.get_current_credentials()
    if current_source:
        console.print(f"Current credentials: {current_source.name} ({current_source.email})")

    # Get key from option or prompt
    if not key:
        key = await safe_ask_async(questionary.password("Enter API key"))

    if not key:
        console.print("[red]No API key provided[/red]")
        raise click.Abort()

    try:
        console.print("[blue]Validating API key...[/blue]")

        # Get key name if not provided
        if not key_name:
            key_name = await safe_ask_async(questionary.text("API Key name", default="hcli"))

        # Install the API key as a new credentials
        source = await auth_service.add_api_key_credentials(key_name, key)

        if not source:
            console.print("[red]Failed to validate API key or get user information[/red]")
            raise click.Abort()

        console.print(f"[green]API key '{source.name}' created successfully![/green]")
        console.print(f"Email: {source.email}")
        console.print(f"Type: {source.type}")

        # Handle default setting
        sources = auth_service.list_credentials()

        if len(sources) == 1:
            # First source - automatically set as default
            console.print("[green]Set as default credentials.[/green]")
        elif set_default:
            # Explicitly requested to set as default
            auth_service.set_default_credentials(source.name)
            console.print(f"[green]'{source.label}' set as default credentials.[/green]")
        else:
            # Ask if user wants to set as default
            set_as_default = await safe_ask_async(
                questionary.confirm(f"User '{source.label}' as the default credentials?", default=True)
            )
            if set_as_default:
                auth_service.set_default_credentials(source.name)
                console.print(f"[green]'{source.label}' set as default credentials.[/green]")
            else:
                default_name = auth_service.get_default_credentials_name()
                console.print(f"Default credentials remains: {default_name}")

        console.print()
        auth_service.show_login_info()

    except Exception as e:
        console.print(f"[red]Failed to install API key: {e}[/red]")
        raise click.Abort()
