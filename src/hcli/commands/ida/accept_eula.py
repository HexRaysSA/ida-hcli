from __future__ import annotations

from pathlib import Path

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida import (
    accept_eula,
    explain_missing_current_installation_directory,
    find_current_ida_install_directory,
    find_standard_installations,
    get_ida_path,
    is_ida_dir,
    MissingCurrentInstallationDirectory,
)


@click.command(name="accept-eula")
@click.argument("path", type=click.Path(path_type=Path), required=False)
def accept_eula_command(path: Path | None) -> None:
    """Accept the IDA EULA for an installation.

    If no PATH is provided, uses the default IDA installation.
    """
    if path is None:
        try:
            # find_current_ida_install_directory returns the binary dir directly
            ida_path = find_current_ida_install_directory()
        except MissingCurrentInstallationDirectory:
            explain_missing_current_installation_directory(console)
            console.print("\nAvailable installations:")
            for install in find_standard_installations():
                console.print(f"  - {install}")
            return
    else:
        install_dir = path.expanduser().resolve()

        if not install_dir.exists():
            console.print(f"[red]Path does not exist: {install_dir}[/red]")
            return

        if not is_ida_dir(install_dir):
            console.print(f"[red]Not a valid IDA installation directory: {install_dir}[/red]")
            console.print("[grey69]The directory must contain the IDA binary.[/grey69]")
            return

        # User provided an installation dir, get the binary path from it
        ida_path = get_ida_path(install_dir)

    console.print(f"[yellow]Accepting EULA for {ida_path}...[/yellow]")
    try:
        accept_eula(ida_path)
        console.print("[green]EULA accepted successfully.[/green]")
    except RuntimeError:
        console.print("[red]Failed to accept EULA: idalib not available.[/red]")
        console.print("[grey69]This may happen for IDA Free, IDA Home, or IDA Classroom editions.[/grey69]")