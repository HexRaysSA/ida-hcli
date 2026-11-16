from __future__ import annotations

from pathlib import Path

import rich_click as click
from rich.console import Console

from hcli.lib.console import console, stderr_console
from hcli.lib.ida import (
    MissingCurrentInstallationDirectory,
    accept_eula,
    explain_missing_current_installation_directory,
    find_current_ida_install_directory,
    find_standard_installations,
    get_ida_path,
    is_ida_dir,
    is_idalib_capable_installation,
)
from hcli.lib.ida.eula import (
    EulaNeedsTerminalError,
    get_eula_action,
    get_eula_consent,
    get_license_text_path,
    is_interactive_terminal,
    render_eula_record,
)


@click.command(name="accept-eula")
@click.option("--yes", "-y", "yes", is_flag=True, default=False, help="Accept the EULA without showing it")
@click.argument("path", type=click.Path(path_type=Path), required=False)
def accept_eula_command(path: Path | None, yes: bool) -> None:
    """Accept the IDA EULA for an installation.

    Shows the EULA (license.txt) in a pager and asks you to accept it.
    Without a terminal, give --yes to accept it.

    If no PATH is provided, uses the default IDA installation.
    """
    if path is None:
        try:
            install_dir = find_current_ida_install_directory()
        except MissingCurrentInstallationDirectory:
            explain_missing_current_installation_directory(console)
            console.print("\nAvailable installations:")
            for install in find_standard_installations():
                console.print(f"  - {install}")
            raise click.exceptions.Exit(1)
    else:
        install_dir = path.expanduser().resolve()

        if not install_dir.exists():
            console.print(f"[red]Path does not exist: {install_dir}[/red]")
            raise click.exceptions.Exit(1)

        if not is_ida_dir(install_dir):
            console.print(f"[red]Not a valid IDA installation directory: {install_dir}[/red]")
            console.print("[grey69]The directory must contain the IDA binary.[/grey69]")
            raise click.exceptions.Exit(1)

    if not is_idalib_capable_installation(install_dir):
        explain_missing_idalib(console)
        raise click.exceptions.Exit(1)

    license_path = get_license_text_path(install_dir)
    action = get_eula_action(True if yes else None, is_interactive_terminal())
    try:
        accepted = get_eula_consent(license_path, action, console)
    except EulaNeedsTerminalError as e:
        console.print(f"error: {e}", markup=False, highlight=False, soft_wrap=True)
        raise click.exceptions.Exit(1)
    except FileNotFoundError:
        console.print(f"error: EULA text not found: {license_path}", markup=False, highlight=False, soft_wrap=True)
        raise click.exceptions.Exit(1)

    if not accepted:
        console.print("[yellow]EULA not accepted.[/yellow]")
        raise click.exceptions.Exit(1)

    try:
        accept_eula(get_ida_path(install_dir))
    except RuntimeError:
        explain_missing_idalib(console)
        raise click.exceptions.Exit(1)
    stderr_console.print(render_eula_record(license_path), markup=False, highlight=False, soft_wrap=True)


def explain_missing_idalib(console: Console) -> None:
    console.print("[red]Failed to accept EULA: idalib not available.[/red]")
    console.print("[grey69]This may happen for IDA Free, IDA Home, or IDA Classroom editions.[/grey69]")
