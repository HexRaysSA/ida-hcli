from __future__ import annotations

from pathlib import Path

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida import (
    MissingCurrentInstallationDirectory,
    accept_eula,
    find_current_ida_install_directory,
    find_standard_installations,
    get_ida_path,
    is_ida_dir,
)


@click.command(name="accept-eula")
@click.argument("path", required=False)
def accept_eula_cmd(path: str | None) -> None:
    """Accept the EULA for an IDA installation.

    If no path is provided, uses the default IDA installation.
    """
    if path is None:
        # Use the default installation
        try:
            install_dir = find_current_ida_install_directory()
        except MissingCurrentInstallationDirectory:
            console.print("[red]No default IDA installation set.[/red]")
            console.print("\nPlease provide an installation path or set a default with:")
            console.print("  [grey69]hcli ida set-default /path/to/IDA/installation/[/grey69]")

            installations = find_standard_installations()
            if installations:
                console.print("\nAvailable installations:")
                for install_dir_found in installations:
                    console.print(f"  - {install_dir_found}")
            return
    else:
        install_dir = Path(path)

        if not install_dir.exists():
            console.print(f"[red]Path does not exist: {install_dir}[/red]")
            return

        if not is_ida_dir(install_dir):
            console.print(f"[red]Not a valid IDA installation directory: {install_dir}[/red]")
            console.print("[grey69]The directory must contain the IDA binary.[/grey69]")
            return

    # Try to determine the product version to check if EULA acceptance is supported
    try:
        # Try to extract version info from directory name
        dir_name = install_dir.name
        if dir_name.endswith(".app"):
            dir_name = dir_name[:-4]

        # Check for known products that don't support idalib
        if any(product in dir_name for product in ["IDA Free", "IDA Home", "IDA Classroom"]):
            console.print(f"[yellow]Warning: {dir_name} may not include idalib.[/yellow]")
            console.print("[yellow]EULA acceptance might not work for this product.[/yellow]")
    except (AttributeError, TypeError):
        # If we can't determine the product (e.g., install_dir.name fails), just continue
        pass

    console.print(f"[yellow]Accepting EULA for: {install_dir}[/yellow]")

    try:
        accept_eula(get_ida_path(install_dir))
        console.print("[green]EULA accepted successfully![/green]")
    except RuntimeError as e:
        console.print(f"[red]Failed to accept EULA: {e}[/red]")
        console.print("[grey69]This usually means idalib is not available for this IDA installation.[/grey69]")
        console.print(
            "[grey69]EULA acceptance requires IDA Professional or other products that include idalib.[/grey69]"
        )
