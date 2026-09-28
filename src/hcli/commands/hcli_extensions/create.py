import click

from hcli.lib.console import console


@click.command(help="Create an hcli extension")
def create():
    console.print("[bold green]You can create a new hcli extension using the following command:[/bold green]")
    console.print()
    console.print("[cyan]pipx run cookiecutter gh:Hex-RaysSA/ida-hcli-extension-template[/cyan]")
    console.print()
    console.print("This will guide you through generating a new extension project based on the official template.")
