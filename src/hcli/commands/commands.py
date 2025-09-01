from __future__ import annotations

import rich_click as click
from rich.table import Table

from hcli.lib.console import console


def collect_all_commands(group: click.Group, parent_path: str = "") -> list[str]:
    """Recursively collect all command paths from a Click group."""
    commands = []

    for name, command in group.commands.items():
        current_path = f"{parent_path} {name}".strip()

        if isinstance(command, click.Group):
            # It's a group, recurse into it
            commands.extend(collect_all_commands(command, current_path))
        else:
            # It's a command, add it to the list
            commands.append(current_path)

    return commands


@click.command(name="commands")
@click.pass_context
def commands(ctx: click.Context):
    """List all available command combinations."""
    root_group = ctx.find_root().command
    if not isinstance(root_group, click.Group):
        console.print("[red]Error: Root command is not a group[/red]")
        return
    all_commands = collect_all_commands(root_group)

    table = Table(title="All Available Commands", show_header=True, header_style="bold blue")
    table.add_column("Command", style="green")
    table.add_column("Description", style="dim")

    for cmd_path in sorted(all_commands):
        # Try to get the command's help text
        try:
            parts = cmd_path.split()
            current_group: click.Command = root_group

            # Navigate to the command
            for part in parts[:-1]:
                if isinstance(current_group, click.Group):
                    current_group = current_group.commands[part]
                else:
                    raise AttributeError("Not a group")

            if isinstance(current_group, click.Group):
                command = current_group.commands[parts[-1]]
            else:
                raise AttributeError("Not a group")
            help_text = command.help or "No description available"
            # Get only the first line of the help text
            help_text = help_text.split("\n")[0].strip()

        except (KeyError, AttributeError):
            help_text = "No description available"

        table.add_row(f"hcli {cmd_path}", help_text)

    console.print(table)
    console.print(f"\n[dim]Total commands: {len(all_commands)}[/dim]")
