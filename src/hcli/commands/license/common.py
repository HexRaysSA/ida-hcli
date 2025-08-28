"""Common license utilities and functions."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import questionary

from hcli.commands.common import safe_ask_async
from hcli.lib.api.license import License, license
from hcli.lib.console import console
from hcli.lib.constants import cli


async def select_licenses(customer_id: str, predicate: Callable[[License], bool] | None = None) -> list[License]:
    """
    Select licenses interactively or return all matching licenses.

    Args:
        customer_id: Customer ID to get licenses for
        predicate: Optional filter function for licenses

    Returns:
        List of selected licenses
    """
    if predicate is None:

        def predicate(lic):
            return True

    licenses = await license.get_licenses(customer_id)
    filtered = [lic for lic in licenses if predicate(lic)]

    if len(filtered) == 1:
        return filtered
    elif len(filtered) == 0:
        console.print("[yellow]No licenses found matching criteria[/yellow]")
        return []
    else:
        # Group licenses by product catalog
        legacy = [lic for lic in filtered if lic.product_catalog == "legacy"]
        subscription = [lic for lic in filtered if lic.product_catalog == "subscription"]
        all_licenses = legacy + subscription

        # Create choices for questionary
        choices: list[questionary.Choice] = []

        # Add legacy licenses
        if legacy:
            choices.append(questionary.Separator("Perpetual licenses:"))
            for lic in legacy:
                choices.append(questionary.Choice(license_to_string(lic), lic))

        # Add subscription licenses
        if subscription:
            choices.append(questionary.Separator("Subscription licenses:"))
            for lic in subscription:
                choices.append(questionary.Choice(license_to_string(lic), lic))

        # Use questionary for selection
        selected = await safe_ask_async(
            questionary.checkbox(
                "Select licenses:",
                choices=choices,
                style=cli.CHECKBOX_STYLE,
            )
        )

        if not selected:
            return []

        if "all" in selected:
            return all_licenses

        return selected


async def download_licenses(customer_id: str, licenses: list[License], target_dir: str) -> None:
    """
    Download multiple licenses.

    Args:
        customer_id: Customer ID
        licenses: List of licenses to download
        target_dir: Target directory for downloads
    """
    for lic in licenses:
        await download_license(customer_id, lic, target_dir, ask_assets=False)


async def download_license(
    customer_id: str,
    lic: License,
    target_dir: str,
    ask_assets: bool = True,
) -> list[str]:
    """
    Download a single license.

    Args:
        customer_id: Customer ID
        lic: License to download
        target_dir: Target directory for downloads
        ask_assets: Whether to ask which asset types to download

    Returns:
        List of downloaded file paths
    """
    results: list[str] = []
    asset_types = lic.asset_types

    if not asset_types:
        console.print("[yellow]This license has no assets to download.[/yellow]")
        return results

    if ask_assets and len(asset_types) > 0:
        asset_type = asset_types[0]
        if len(asset_types) > 1:
            console.print(f"\n[bold]Available asset types for license {lic.pubhash}:[/bold]")
            for i, asset in enumerate(asset_types, 1):
                console.print(f"  {i}. {asset}")

            selection = await safe_ask_async(
                questionary.select(
                    "Select asset type:",
                    choices=[questionary.Choice(f"{i}. {asset}", str(i)) for i, asset in enumerate(asset_types, 1)],
                    default="1",
                    style=cli.SELECT_STYLE,
                )
            )
            asset_type = asset_types[int(selection) - 1]

        result = await download_license_asset(customer_id, lic, asset_type, target_dir)
        if result:
            results.append(result)
    else:
        # Download all asset types
        for asset_type in asset_types:
            result = await download_license_asset(customer_id, lic, asset_type, target_dir)
            if result:
                results.append(result)

    return results


async def download_license_asset(
    customer_id: str,
    lic: License,
    asset_type: str,
    target_dir: str,
) -> str | None:
    """
    Download a specific license asset.

    Args:
        customer_id: Customer ID
        lic: License object
        asset_type: Type of asset to download
        target_dir: Target directory for download

    Returns:
        Downloaded file path or None if failed
    """
    try:
        if not lic.pubhash:
            console.print(f"[red]License has no ID for asset {asset_type}[/red]")
            return None
        filename = await license.download_license(customer_id, lic.pubhash, asset_type, target_dir)
        if filename:
            console.print(f"[green]License {asset_type} for {lic.pubhash} downloaded as: {filename}[/green]")
            return filename
        else:
            console.print("[red]Failed to download license[/red]")
            return None
    except Exception as e:
        console.print(f"[red]Error downloading license: {e}[/red]")
        return None


def license_to_string(lic: License) -> str:
    """
    Convert license object to human-readable string.

    Args:
        lic: License object

    Returns:
        Formatted license string
    """
    text = "does not expire"

    if lic.end_date:
        try:
            end_date = datetime.fromisoformat(lic.end_date.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)

            if end_date < now:
                # Calculate time since expiration
                delta = now - end_date
                if delta.days > 365:
                    years = delta.days // 365
                    text = f"expired {years} year{'s' if years != 1 else ''} ago"
                elif delta.days > 30:
                    months = delta.days // 30
                    text = f"expired {months} month{'s' if months != 1 else ''} ago"
                elif delta.days > 0:
                    text = f"expired {delta.days} day{'s' if delta.days != 1 else ''} ago"
                else:
                    text = "expired today"
            else:
                # Calculate time until expiration
                delta = end_date - now
                if delta.days > 365:
                    years = delta.days // 365
                    text = f"expires in {years} year{'s' if years != 1 else ''}"
                elif delta.days > 30:
                    months = delta.days // 30
                    text = f"expires in {months} month{'s' if months != 1 else ''}"
                elif delta.days > 0:
                    text = f"expires in {delta.days} day{'s' if delta.days != 1 else ''}"
                else:
                    text = "expires today"
        except (ValueError, TypeError):
            # Fallback if date parsing fails
            text = f"expires {lic.end_date}"

    # Get decompilers and other addons
    decompilers = [
        addon.product.code
        for addon in (lic.addons or [])
        if addon.product and addon.product.product_subtype == "DECOMPILER"
    ]
    other = [
        addon.product.code
        for addon in (lic.addons or [])
        if addon.product and addon.product.product_subtype != "DECOMPILER"
    ]

    suffix = ""
    if decompilers:
        suffix += f"{len(decompilers)} decompiler{'s' if len(decompilers) != 1 else ''} "
    if other:
        suffix += f"[{', '.join(other)}]"

    edition_name = lic.edition.edition_name if lic.edition else "Unknown"
    return f"{lic.pubhash} {edition_name} [{lic.license_type}] {text} {suffix}".strip()


def ensure_target_directory(target_dir: str) -> str:
    """
    Ensure target directory exists and return the absolute path.

    Args:
        target_dir: Target directory path

    Returns:
        Absolute path to the target directory
    """
    path = Path(target_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return str(path)
