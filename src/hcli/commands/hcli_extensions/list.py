from importlib.metadata import entry_points

import click

from hcli.lib.console import console


@click.command(help="List hcli extensions")
def list_extensions():
    eps = entry_points()
    extension_eps = list(eps.select(group="hcli.extensions"))

    if extension_eps:
        extensions_list = ", ".join([ep.name for ep in extension_eps])
        console.print(f"[bold green]Extensions:[/bold green] [cyan]{extensions_list}[/cyan]")
    else:
        console.print("No extensions installed")
