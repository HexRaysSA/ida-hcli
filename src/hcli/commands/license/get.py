from __future__ import annotations

import rich_click as click

from hcli.commands.common import select_customer
from hcli.commands.license.common import (
    download_license,
    ensure_target_directory,
    select_licenses,
)
from hcli.lib.api.license import License, license
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console


@auth_command()
@click.option("-i", "--id", "lid", help="License ID (e.g., 48-307B-71D4-46)")
@click.option(
    "-p",
    "--plan",
    "plan",
    type=click.Choice(["subscription", "legacy"]),
    help="Plan type: subscription or legacy",
)
@click.option("-t", "--type", "type", help="License type (e.g., IDAPRO, IDAHOME, LICENSE_SERVER)")
@click.option("-a", "--all", "all", is_flag=True, help="Get all matching licenses")
@click.option("--output-dir", "output_dir", default="./", help="Output directory for license files")
@async_command
async def get_license(
    lid: str | None = None,
    plan: str | None = None,
    type: str | None = None,
    all: bool = False,
    output_dir: str = "./",
) -> None:
    """Download license files with optional filtering."""
    # Select customer
    customer_obj = await select_customer()
    if not customer_obj:
        return

    # Create predicate function for filtering licenses
    def predicate(lic: License) -> bool:
        return (
            (not lid or lic.pubhash == lid)
            and (lic.status == "active")
            and (not type or lic.product_code == type)
            and (not plan or lic.product_catalog == plan)
        )

    # Get licenses
    if not customer_obj.id:
        console.print("[red]Customer ID not available[/red]")
        return
    customer_id_str = str(customer_obj.id)
    if all:
        licenses = await license.get_licenses(customer_id_str)
        selected = [lic for lic in licenses if predicate(lic)]
    else:
        selected = await select_licenses(customer_id_str, predicate)

    if not selected:
        console.print("[yellow]No licenses found matching criteria[/yellow]")
        return

    # Ensure output directory exists
    target_dir = ensure_target_directory(output_dir)

    # Download licenses
    console.print(f"\n[bold]Downloading {len(selected)} license(s) to {target_dir}[/bold]")

    for lic in selected:
        await download_license(customer_id_str, lic, target_dir, ask_assets=False)

    console.print("[green]Download completed[/green]")
