"""Plugin search command."""

from __future__ import annotations

import questionary
import rich_click as click

from hcli.commands.common import safe_ask_async
from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.constants import cli


@click.command()
@click.argument("query", required=False)
@click.option("--limit", default=20, help="Maximum number of results to show")
@async_command
async def search_plugins(query: str, limit: int) -> None:
    """Search for plugins in the repository."""

    if not query:
        query = await safe_ask_async(questionary.text("Enter search query:", style=cli.SELECT_STYLE))

    if not query.strip():
        console.print("[red]Search query cannot be empty[/red]")
        return

    console.print(f"[bold]Searching for plugins matching '{query}'...[/bold]")

    try:
        raise NotImplementedError("Plugin search")
    except Exception as e:
        console.print(f"[red]Search failed: {e}[/red]")
