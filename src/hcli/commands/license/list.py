from __future__ import annotations

import rich_click as click
from rich.table import Table

from hcli.commands.common import select_customer
from hcli.lib.api.license import license
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console


@click.option(
    "-p",
    "--plan",
    type=click.Choice(["subscription", "legacy"]),
    help="Filter by plan type: subscription or legacy",
)
@auth_command()
@async_command
async def list_licenses(plan: str | None) -> None:
    """List available licenses with rich formatting."""
    # Select customer
    customer_obj = await select_customer()
    if not customer_obj:
        return

    # Get licenses
    if not customer_obj.id:
        console.print("[red]Customer ID not available[/red]")
        return
    licenses = await license.get_licenses(str(customer_obj.id))

    if not licenses:
        console.print("[yellow]No licenses found[/yellow]")
        return

    # Filter by plan if specified
    if plan:
        licenses = [lic for lic in licenses if lic.product_catalog == plan]
        if not licenses:
            console.print(f"[yellow]No {plan} licenses found[/yellow]")
            return

    # Group licenses by product catalog
    legacy_licenses = [lic for lic in licenses if lic.product_catalog == "legacy"]
    subscription_licenses = [lic for lic in licenses if lic.product_catalog == "subscription"]

    console.print(f"\n[bold]Licenses for customer [{customer_obj.id}]:[/bold]")

    # Display legacy licenses
    if legacy_licenses:
        console.print(f"\n[cyan bold]Perpetual Licenses ({len(legacy_licenses)}):[/cyan bold]")
        _display_licenses_table(legacy_licenses)

    # Display subscription licenses
    if subscription_licenses:
        console.print(f"\n[cyan bold]Subscription Licenses ({len(subscription_licenses)}):[/cyan bold]")
        _display_licenses_table(subscription_licenses)

    console.print(f"\n[dim]Total: {len(licenses)} license(s)[/dim]")


def _display_licenses_table(licenses) -> None:
    """Display licenses in a formatted table."""
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Edition", style="green")
    table.add_column("Type", style="yellow")
    table.add_column("Status", style="blue")
    table.add_column("Expiration", style="red")
    table.add_column("Addons", style="magenta")

    for lic in licenses:
        # Format status with color
        if lic.status == "active":
            status = "[green]Active[/green]"
        elif lic.status == "expired":
            status = "[red]Expired[/red]"
        else:
            status = f"[yellow]{lic.status.title()}[/yellow]"

        # Format expiration
        expiration_text = "Never"
        if lic.end_date:
            try:
                from datetime import datetime, timezone

                end_date = datetime.fromisoformat(lic.end_date.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)

                if end_date < now:
                    delta = now - end_date
                    if delta.days > 365:
                        years = delta.days // 365
                        expiration_text = f"[red]{years}y ago[/red]"
                    elif delta.days > 30:
                        months = delta.days // 30
                        expiration_text = f"[red]{months}mo ago[/red]"
                    else:
                        expiration_text = f"[red]{delta.days}d ago[/red]"
                else:
                    delta = end_date - now
                    if delta.days > 365:
                        years = delta.days // 365
                        expiration_text = f"[yellow]{years}y[/yellow]"
                    elif delta.days > 30:
                        months = delta.days // 30
                        expiration_text = f"[yellow]{months}mo[/yellow]"
                    else:
                        expiration_text = f"[yellow]{delta.days}d[/yellow]"
            except (ValueError, TypeError):
                expiration_text = lic.end_date

        # Format addons
        decompilers = [addon.product.code for addon in lic.addons if addon.product.product_subtype == "DECOMPILER"]
        other = [addon.product.code for addon in lic.addons if addon.product.product_subtype != "DECOMPILER"]

        addon_parts = []
        if decompilers:
            addon_parts.append(f"{len(decompilers)} decompiler(s)")
        if other:
            addon_parts.append(", ".join(other))

        addons = " + ".join(addon_parts) if addon_parts else "None"

        table.add_row(
            lic.pubhash,
            lic.edition.edition_name,
            lic.license_type,
            status,
            expiration_text,
            addons,
        )

    console.print(table)
