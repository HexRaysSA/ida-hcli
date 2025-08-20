from __future__ import annotations

import questionary
import rich_click as click

from hcli.commands.common import safe_ask_async
from hcli.lib.auth import get_auth_service
from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.constants import cli


@click.command(name="switch")
@click.argument("name", required=False)
@async_command
async def switch_credentials(name: str | None) -> None:
    """Switch the default credentials."""
    auth_service = get_auth_service()
    auth_service.init()

    sources = auth_service.list_credentials()

    if not sources:
        console.print("[yellow]No credentials found.[/yellow]")
        console.print("Use '[bold]hcli login[/bold]' or '[bold]hcli auth key install[/bold]' to add credentials.")
        return

    if len(sources) == 1:
        console.print("[yellow]Only one credentials available. No switching needed.[/yellow]")
        source = sources[0]
        label = getattr(source, "label", source.email)
        console.print(f"Current source: {source.name} ({label})")
        return

    current_default = auth_service.get_default_credentials_name()

    if name:
        # Switch to specific source
        if name not in [s.name for s in sources]:
            console.print(f"[red]Credentials '{name}' not found.[/red]")
            console.print(f"Available credentials: {', '.join(s.name for s in sources)}")
            return

        if name == current_default:
            console.print(f"[yellow]'{name}' is already the default credentials.[/yellow]")
            return

        if auth_service.set_default_credentials(name):
            console.print(f"[green]Switched default credentials to '{name}'.[/green]")
            auth_service.show_login_info()
        else:
            console.print(f"[red]Failed to switch to credentials '{name}'.[/red]")
        return

    # Interactive selection
    console.print("[bold]Available credentials:[/bold]")

    choices = []
    for source in sources:
        label = getattr(source, "label", source.email)
        choice_text = f"{label} ({source.type})"
        choices.append(questionary.Choice(title=choice_text, value=source.name))

    # Add cancel option
    choices.append(questionary.Choice(title="Cancel", value=None))

    selected = await safe_ask_async(
        questionary.select(
            "Select new default credentials:", choices=choices, default=current_default, style=cli.SELECT_STYLE
        )
    )

    if selected is None:
        console.print("[yellow]Switch cancelled.[/yellow]")
        return

    if auth_service.set_default_credentials(selected):
        console.print(f"[green]Switched default credentials to '{selected}'.[/green]")
        console.print()
        auth_service.show_login_info()
    else:
        console.print(f"[red]Failed to switch to credentials '{selected}'.[/red]")
