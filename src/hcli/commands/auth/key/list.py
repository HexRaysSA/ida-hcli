from __future__ import annotations

from datetime import datetime

import rich_click as click
from rich.table import Table

from hcli.lib.api.keys import keys
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console


def format_datetime(date_str: str) -> str:
    """Format datetime string for display."""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d %Y")
    except (ValueError, AttributeError):
        return "Unknown"


def format_relative_time(date_str: str | None) -> str:
    """Format relative time for last used."""
    if not date_str:
        return "never"
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo)
        diff = now - dt

        if diff.days > 0:
            return f"{diff.days} day{'s' if diff.days != 1 else ''} ago"
        elif diff.seconds > 3600:
            hours = diff.seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        elif diff.seconds > 60:
            minutes = diff.seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        else:
            return "just now"
    except (ValueError, AttributeError):
        return "unknown"


@auth_command()
@async_command
async def list_keys() -> None:
    """List all API keys."""
    try:
        api_keys = await keys.get_keys()

        if not api_keys:
            console.print("[yellow]No API keys found.[/yellow]")
            return

        # Create Rich table
        table = Table(title="API Keys")
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Created", style="green")
        table.add_column("Last Used", style="yellow")
        table.add_column("Requests", style="magenta", justify="right")

        # Add rows to table
        for key in api_keys:
            table.add_row(
                f"[underline]{key.name}[/underline]",
                format_datetime(key.created_at),
                format_relative_time(key.last_used_at),
                str(key.request_count),
            )

        console.print(table)

    except Exception as e:
        console.print(f"[red]Failed to list API keys: {e}[/red]")
        raise click.Abort()
