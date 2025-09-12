from __future__ import annotations

import questionary
import rich_click as click

from hcli.commands.common import safe_ask_async
from hcli.lib.api.keys import keys
from hcli.lib.commands import async_command, require_auth
from hcli.lib.console import console
from hcli.lib.constants import cli


@click.command()
@require_auth
@async_command
async def revoke() -> None:
    """Revoke an API key."""
    try:
        # Get all keys
        api_keys = await keys.get_keys()

        if not api_keys:
            console.print("[yellow]No API keys found to revoke.[/yellow]")
            return

        # Create choices for questionary.select
        choices = []
        for key in api_keys:
            created_date = key.created_at[:10] if key.created_at else "Unknown"
            choice_label = f"{key.name} (Created: {created_date}, Requests: {key.request_count})"
            choices.append(choice_label)

        # Use questionary.select to get user selection
        selection = await safe_ask_async(
            questionary.select(
                "Select API key to revoke:",
                choices=choices,
                style=cli.SELECT_STYLE,
            )
        )

        # If user cancelled (Ctrl+C or ESC)
        if selection is None:
            console.print("[yellow]Operation cancelled.[/yellow]")
            return

        # Find the selected key based on the choice
        selected_index = choices.index(selection)
        selected_key = api_keys[selected_index]

        # Confirm revocation
        if not await safe_ask_async(questionary.confirm(f"Do you want to revoke the key named '{selected_key.name}'?")):
            console.print("[yellow]Revocation cancelled.[/yellow]")
            return

        # Revoke the key
        console.print(f"[blue]Revoking API key '[bold]{selected_key.name}[/bold]'...[/blue]")
        await keys.revoke_key(selected_key.name)
        console.print(f"[green]API key '[bold]{selected_key.name}[/bold]' has been revoked.[/green]")

    except Exception as e:
        console.print(f"[red]Failed to revoke API key: {e}[/red]")
        raise click.Abort()
