from __future__ import annotations

import rich_click as click
from rich.table import Table

from hcli.lib.auth import get_auth_service
from hcli.lib.console import console


@click.command(name="list")
def list_credentials() -> None:
    """List all credentials."""
    auth_service = get_auth_service()
    auth_service.init()

    sources = auth_service.list_credentials()

    if not sources:
        console.print("[yellow]No credentials found.[/yellow]")
        console.print("Use '[bold]hcli login[/bold]' or '[bold]hcli auth key install[/bold]' to add credentials.")
        return

    console.print(f"[bold]Credentials ({len(sources)}):[/bold]")
    console.print()

    # Create table
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Label", style="white", width=45)
    table.add_column("Type", style="blue", width=12)
    table.add_column("Status", style="green", width=15)
    table.add_column("Created", style="yellow", width=20)
    table.add_column("Last Used", style="dim", width=20)

    current_source = auth_service.get_current_credentials()
    default_name = auth_service.get_default_credentials_name()

    # Sort sources: default first, then by last used
    def sort_key(source):
        is_default = 1 if source.name == default_name else 0
        return (-is_default, source.last_used)

    sources.sort(key=sort_key, reverse=True)

    for source in sources:
        status_parts = []
        if current_source and source.name == current_source.name:
            status_parts.append("Active")
        if source.name == default_name:
            status_parts.append("Default")

        status = ", ".join(status_parts) if status_parts else "-"

        # Format dates
        try:
            from datetime import datetime

            created = datetime.fromisoformat(source.created_at.replace("Z", "+00:00"))
            created_str = created.strftime("%Y-%m-%d %H:%M")

            last_used = datetime.fromisoformat(source.last_used.replace("Z", "+00:00"))
            last_used_str = last_used.strftime("%Y-%m-%d %H:%M")
        except Exception:
            created_str = source.created_at[:16] if source.created_at else "N/A"
            last_used_str = source.last_used[:16] if source.last_used else "N/A"

        # Truncate long names/labels
        label_display = getattr(source, "label", source.email)  # Fallback to email for backward compatibility
        label_display = label_display[:43] + "..." if len(label_display) > 45 else label_display

        table.add_row(label_display, source.type.title(), status, created_str, last_used_str)

    console.print(table)

    # Show summary
    console.print()
    if current_source:
        console.print(f"[green]Current: {current_source.name} ({current_source.email})[/green]")
    else:
        console.print("[red]No active credentials[/red]")

    if default_name:
        console.print(f"[blue]Default: {default_name}[/blue]")
    else:
        console.print("[yellow]No default credentials set[/yellow]")
