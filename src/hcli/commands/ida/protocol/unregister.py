from __future__ import annotations

import platform

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida.protocol import unregister_protocol_handler


@click.command(name="unregister")
def unregister() -> None:
    """Remove hcli protocol handlers for ida:// URLs.

    This command removes hcli as the handler for ida:// URLs on your system.

    \b
    The removal process varies by platform:
    - macOS: Removes the AppleScript application and unregisters from Launch Services
    - Windows: Removes registry entries for the ida:// protocol
    - Linux: Removes the desktop entry and unregisters from xdg-mime
    """
    current_platform = platform.system().lower()

    console.print(f"[blue]Removing hcli protocol handlers for {current_platform}...[/blue]")

    try:
        unregister_protocol_handler()

        console.print("[green]✓ Protocol handler removal complete![/green]")
        console.print("[yellow]ida:// links will no longer open with hcli.[/yellow]")

    except Exception as e:
        console.print(f"[red]Unregistration failed: {e}[/red]")
        raise
