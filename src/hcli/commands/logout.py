from __future__ import annotations

import questionary
import rich_click as click
from rich.table import Table

from hcli.commands.common import safe_ask_async
from hcli.lib.auth import get_auth_service
from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.constants import cli


@click.command()
@click.option("-n", "--name", help="Name of specific credentials to remove")
@click.option("-a", "--all", "remove_all", is_flag=True, help="Remove all credentials")
@async_command
async def logout(name: str | None, remove_all: bool) -> None:
    """Log out and remove stored credentials."""
    auth_service = get_auth_service()
    auth_service.init()

    sources = auth_service.list_credentials()

    if not sources:
        console.print("[yellow]No credentials found.[/yellow]")
        return

    if remove_all:
        # Remove all sources
        confirm = await safe_ask_async(questionary.confirm(f"Remove all {len(sources)} credentials?", default=False))
        if confirm:
            removed_count = 0
            for source in sources:
                if auth_service.remove_credentials(source.name):
                    removed_count += 1
            console.print(f"[green]Removed {removed_count} credentials.[/green]")
        else:
            console.print("[yellow]Logout cancelled.[/yellow]")
        return

    if name:
        # Remove specific source
        source_to_remove = None
        for source in sources:
            if source.name == name:
                source_to_remove = source
                break

        if not source_to_remove:
            console.print(f"[red]Credentials '{name}' not found.[/red]")
            return

        confirm = await safe_ask_async(
            questionary.confirm(
                f"Remove credentials '{source_to_remove.name}' ({source_to_remove.email})?", default=False
            )
        )
        if confirm:
            if auth_service.remove_credentials(source_to_remove.name):
                console.print(f"[green]Removed credentials '{source_to_remove.name}'.[/green]")
            else:
                console.print(f"[red]Failed to remove credentials '{source_to_remove.name}'.[/red]")
        else:
            console.print("[yellow]Logout cancelled.[/yellow]")
        return

    # Auto-logout if only one source (simplified UX)
    if len(sources) == 1:
        source = sources[0]
        if auth_service.remove_credentials(source.name):
            console.print("[green]Logged out.[/green]")
        else:
            console.print("[red]Failed to logout.[/red]")
        return

    # Interactive selection for multiple sources
    console.print("[bold]Available credentials:[/bold]")

    # Display sources table
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name", style="cyan", width=20)
    table.add_column("Label", style="white", width=35)
    table.add_column("Type", style="blue", width=12)
    table.add_column("Status", style="green", width=10)
    table.add_column("Last Used", style="yellow", width=20)

    current_source = auth_service.get_current_credentials()
    default_name = auth_service.get_default_credentials_name()

    for source in sources:
        status_parts = []
        if current_source and source.name == current_source.name:
            status_parts.append("Active")
        if source.name == default_name:
            status_parts.append("Default")

        status = ", ".join(status_parts) if status_parts else "-"

        # Format last used date
        try:
            from datetime import datetime

            last_used = datetime.fromisoformat(source.last_used.replace("Z", "+00:00"))
            last_used_str = last_used.strftime("%Y-%m-%d %H:%M")
        except Exception:
            last_used_str = source.last_used[:16] if source.last_used else "N/A"

        # Get label with fallback
        label_display = getattr(source, "label", source.email)

        table.add_row(source.name, label_display, source.type.title(), status, last_used_str)

    console.print(table)
    console.print()

    # Create choices for selection
    choices = []
    for source in sources:
        status_info = ""
        if current_source and source.name == current_source.name:
            status_info += " (Active)"
        if source.name == default_name:
            status_info += " (Default)"

        label = getattr(source, "label", source.email)
        choice_text = f"{source.name} - {label} ({source.type.title()}){status_info}"
        choices.append(questionary.Choice(title=choice_text, value=source.name))

    # Add option to cancel
    choices.append(questionary.Choice(title="Cancel", value=None))

    selected = await safe_ask_async(
        questionary.select("Select credentials to remove:", choices=choices, style=cli.SELECT_STYLE)
    )

    if selected is None:
        console.print("[yellow]Logout cancelled.[/yellow]")
        return

    # Find the selected source
    source_to_remove = None
    for source in sources:
        if source.name == selected:
            source_to_remove = source
            break

    if not source_to_remove:
        console.print("[red]Selected source not found.[/red]")
        return

    # Final confirmation
    console.print("[red]You are about to remove credentials:[/red]")
    console.print(f"  Name: {source_to_remove.name}")
    console.print(f"  Email: {source_to_remove.email}")
    console.print(f"  Type: {source_to_remove.type.title()}")

    confirm = await safe_ask_async(questionary.confirm("Are you sure?", default=False))

    if confirm:
        if auth_service.remove_credentials(source_to_remove.name):
            console.print(f"[green]Successfully removed credentials '{source_to_remove.name}'.[/green]")

            # Show remaining sources
            remaining_sources = auth_service.list_credentials()
            if remaining_sources:
                console.print(f"\nRemaining sources: {len(remaining_sources)}")
                current_source = auth_service.get_current_credentials()
                if current_source:
                    console.print(f"Current default: {current_source.name} ({current_source.email})")
                else:
                    console.print("No default source set")
            else:
                console.print("[yellow]No credentials remaining. You are now logged out.[/yellow]")
        else:
            console.print(f"[red]Failed to remove credentials '{source_to_remove.name}'.[/red]")
    else:
        console.print("[yellow]Logout cancelled.[/yellow]")
