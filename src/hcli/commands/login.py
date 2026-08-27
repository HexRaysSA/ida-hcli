from __future__ import annotations

import os
import sys
import webbrowser

import questionary
import rich_click as click

from hcli.commands.common import safe_ask_async
from hcli.lib.auth import get_auth_service
from hcli.lib.commands import async_command
from hcli.lib.console import console


def _running_headless() -> bool:
    """Return True when the browser+loopback flow cannot work here.

    The loopback redirect targets 127.0.0.1, which is only reachable from the
    machine hcli runs on. In a remote shell the browser lives elsewhere, so the
    out-of-band paste-the-code flow is the only option. We treat a session as
    headless when it is an SSH connection, a Linux box with no display server,
    or a host with no registered web browser.
    """
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return True
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return True
    try:
        webbrowser.get()
    except webbrowser.Error:
        return True
    return False


@click.command()
@click.option("-f", "--force", is_flag=True, help="Force account selection.")
@click.option("-n", "--name", help="Custom name for the credentials")
@click.option(
    "--browser/--no-browser",
    "browser",
    default=None,
    help="Force (--browser) or skip (--no-browser) the local browser flow. "
    "Auto-detected for SSH/headless sessions when omitted.",
)
@async_command
async def login(force: bool, name: str | None, browser: bool | None) -> None:
    """Log in to the Hex-Rays portal and create new credentials."""
    auth_service = get_auth_service()
    auth_service.init()

    # Decide between the browser+loopback flow and the headless paste-the-code
    # (out-of-band) flow. An explicit --browser/--no-browser wins; otherwise we
    # auto-detect remote/headless sessions.
    use_oob = _running_headless() if browser is None else not browser

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

    # Run the OAuth Authorization Code + PKCE flow.
    if use_oob:
        try:
            authorize_url = auth_service.begin_oob_login(force=force)
        except Exception as e:
            console.print(f"[red]Could not reach the authorization server: {e}[/red]")
            raise click.Abort()

        if browser is None:
            console.print("[yellow]No local browser detected; using paste-the-code login.[/yellow]")
        console.print("Open this URL in a browser, approve the request, then paste the code below:")
        console.print(authorize_url)
        code = await safe_ask_async(questionary.text("Authorization code"))
        source = auth_service.complete_oob_login(code, name=name)
    else:
        source = await auth_service.login_interactive(name=name, force=force)

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
