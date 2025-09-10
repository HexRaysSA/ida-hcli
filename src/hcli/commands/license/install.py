from __future__ import annotations

from pathlib import Path

import questionary
import rich_click as click

from hcli.commands.common import safe_ask_async
from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.constants import cli
from hcli.lib.ida import (
    find_standard_installations,
    get_ida_user_dir,
)
from hcli.lib.ida import (
    install_license as ida_install_license,
)


@click.command()
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.argument("ida_dir", required=False)
@async_command
async def install_license(file: Path, ida_dir: str | None) -> None:
    """Install a license file to an IDA Pro installation directory."""
    # Find IDA installations
    suggested = find_standard_installations()
    user_dir = get_ida_user_dir()

    # Build options list
    options = []
    if user_dir:
        options.append(f"1. {user_dir} (user directory)")

    for i, installation in enumerate(suggested, len(options) + 1):
        options.append(f"{i}. {installation}")

    options.append(f"{len(options) + 1}. Other (specify custom path)")

    # Select target directory
    if ida_dir:
        target = Path(ida_dir)
    else:
        console.print("\n[bold]Where do you want to install the license?[/bold]")
        for option in options:
            console.print(f"  {option}")

        # Get selection
        choices_with_labels = []
        for i, option in enumerate(options, 1):
            choices_with_labels.append(option)

        default_option = options[0] if user_dir else (options[1] if suggested else options[0])
        selection = await safe_ask_async(
            questionary.select(
                "Select installation:",
                choices=choices_with_labels,
                default=default_option,
                style=cli.SELECT_STYLE,
            )
        )

        # Extract the choice number from the selection
        choice_num = int(selection.split(".")[0])

        if choice_num == 1 and user_dir:
            target = Path(user_dir)
        elif choice_num <= len(suggested) + (1 if user_dir else 0):
            # Standard installation
            idx = choice_num - (2 if user_dir else 1)
            target = suggested[idx]
        else:
            # Custom path
            target = await safe_ask_async(questionary.path("Enter the target directory path", default="."))

    # Validate target directory
    target_path = Path(target).expanduser().resolve()
    print(f"==> {target_path}")
    if not target_path.exists():
        console.print(f"[red]Target directory does not exist: {target_path}[/red]")
        create = await safe_ask_async(
            questionary.select(
                "Create directory?", choices=["y. Yes", "n. No"], default="y. Yes", style=cli.SELECT_STYLE
            )
        )
        if create.startswith("y"):
            target_path.mkdir(parents=True, exist_ok=True)
        else:
            return

    try:
        # Install the license
        ida_install_license(file, target_path)
        console.print(f"[green]License installed successfully in {target_path}[/green]")

        # Show installed file info
        installed_file = target_path / file.name
        if installed_file.exists():
            console.print(f"[dim]License file installed in: {installed_file}[/dim]")

    except Exception as e:
        console.print(f"[red]Failed to install license: {e}[/red]")
        raise click.Abort()
