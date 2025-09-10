from __future__ import annotations

import questionary
import rich_click as click

from hcli.commands.common import safe_ask_async
from hcli.lib.auth import get_auth_service
from hcli.lib.commands import async_command
from hcli.lib.config import config_store
from hcli.lib.console import console
from hcli.lib.constants import cli


@click.command()
@click.option("-f", "--force", is_flag=True, help="Force account selection.")
@click.option("-n", "--name", help="Custom name for the credentials")
@async_command
async def login(force: bool, name: str | None) -> None:
    """Log in to the Hex-Rays portal and create new credentials."""
    auth_service = get_auth_service()
    auth_service.init()

    # Show current login status
    if auth_service.is_logged_in() and not force:
        sources = auth_service.list_credentials()
        current_source = auth_service.get_current_credentials()
        if not current_source:
            console.print("[red]No valid credentials found[/red]")
            return

        if len(sources) == 1:
            # Simplified message for single source
            console.print(f"[green]You are already logged in as {current_source.email}.[/green]")
            add_another = await safe_ask_async(
                questionary.confirm("Would you like to login as another user?", default=False)
            )
        else:
            # Detailed message for multiple sources
            console.print("[green]You are already logged in.[/green]")
            if current_source:
                console.print(f"Current source: {current_source.name} ({current_source.email})")
            add_another = await safe_ask_async(
                questionary.confirm("Would you like to add another credentials?", default=False)
            )

        if not add_another:
            return

    # Get the last used email for suggestions
    current_email = config_store.get_string("login.email", "")

    # Choose authentication method
    choices = ["Google OAuth", "Email (OTP)"]
    selected = await safe_ask_async(
        questionary.select("Choose login method:", choices=choices, default="Google OAuth", style=cli.SELECT_STYLE)
    )

    source = None

    if selected == "Google OAuth":
        # Google OAuth login
        console.print("[blue]Starting OAuth login...[/blue]")
        source = await auth_service.login_interactive(name=name, force=force)

    elif selected == "Email (OTP)":
        # Email OTP login
        email = await safe_ask_async(questionary.text("Email address", default=current_email if current_email else ""))

        try:
            console.print(f"[blue]Sending OTP to {email}...[/blue]")
            await auth_service.login_otp(email, name=name, force=force)

            otp = await safe_ask_async(questionary.text("Enter the code received by email"))

            source = auth_service.verify_otp(email, otp, name=name)
            if source:
                config_store.set_string("login.email", email)
                console.print("[green]Login successful![/green]")
            else:
                console.print("[red]Login failed. Invalid OTP.[/red]")
        except Exception as e:
            console.print(f"[red]Login failed: {e}[/red]")
            raise click.Abort()

    # Show results
    if source:
        sources_count_before = len(auth_service.list_credentials()) - 1  # Subtract the new source

        if sources_count_before == 0:
            # First login - automatically set as default
            console.print(f"[green]Logged in as {source.email}[/green]")
            auth_service.set_default_credentials(source.name)
        else:
            # Additional credentials - detailed message
            console.print(f"[green]Credentials '{source.label}' created successfully![/green]")
            console.print(f"Email: {source.email}")
            console.print(f"Type: {source.type}")

            # Ask if user wants to set as default
            set_default = await safe_ask_async(
                questionary.confirm(f"Set '{source.name}' as the default credentials?", default=True)
            )
            if set_default:
                auth_service.set_default_credentials(source.name)
                console.print(f"[green]'{source.name}' set as default credentials.[/green]")

        # Show login info only for multi-source scenarios
        if sources_count_before > 0:
            console.print()
            auth_service.show_login_info()
    else:
        console.print("[red]Login failed.[/red]")
