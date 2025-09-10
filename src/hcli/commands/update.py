from __future__ import annotations

import re

import questionary
import rich_click as click
from semantic_version import SimpleSpec

from hcli.commands.common import safe_ask_async
from hcli.env import ENV
from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.update.release import (
    GitHubRepo,
    get_assets,
    get_compatible_version,
    update_asset,
)
from hcli.lib.update.version import (
    is_binary,
)
from hcli.lib.util.io import get_arch, get_executable_path, get_os


@click.command()
@click.option("-f", "--force", is_flag=True, help="Force update.")
@click.option(
    "-m",
    "--mode",
    default="auto",
    type=click.Choice(["auto", "pypi", "binary"]),
    help="Update source (auto detects based on installation type).",
    hidden=True,
)
@click.option(
    "--check-only",
    is_flag=True,
    help="Only check for updates, do not suggest installation.",
)
@click.option(
    "--auto-install", is_flag=True, help="Automatically install update if available (for binary version only)."
)
@click.option(
    "--include-prereleases",
    is_flag=True,
    help="Include pre-release versions when checking GitHub (for binary version only).",
)
@async_command
async def update(
    force: bool = False,
    mode: str = "auto",
    auto_install: bool = False,
    check_only: bool = False,
    include_prereleases: bool = False,
) -> None:
    """Check for hcli updates."""

    # Auto-detect mode if not specified
    if mode == "auto":
        if is_binary():
            mode = "binary"  # Use GitHub for frozen binaries
        else:
            mode = "pypi"  # Use PyPI for non-frozen installs

    console.print(f"[bold]Checking for updates ({mode})...[/bold]")

    # Handle GitHub binary updates specially for frozen executables
    if mode == "binary" or is_binary():
        try:
            repo = GitHubRepo.from_url(ENV.HCLI_GITHUB_URL)

            # get current & latest
            current_version = ENV.HCLI_VERSION
            operator = ">=" if force else ">"
            latest_version = get_compatible_version(
                repo, SimpleSpec(f"{operator}{current_version}"), include_dev=include_prereleases
            )

            if latest_version is None:
                console.print(f"[green]Already using the latest version ({current_version})[/green]")
                return

            latest_tag = getattr(latest_version, "_origin_tag_name", None)
            assert latest_tag is not None, "Latest version tag not found"
            mask = f".*-{get_os()}-{get_arch()}.*"
            assets = get_assets(repo, latest_tag, re.compile(mask))

            if latest_tag and len(assets) == 1:
                console.print(f"[yellow]Update available: {current_version} → {latest_version}[/yellow]")

                # Skip confirmation if auto_install is enabled
                if not auto_install:
                    confirm = await safe_ask_async(
                        questionary.confirm(f"Do you want to install the update to {latest_version}?", default=True),
                        "Update cancelled.",
                    )
                    if not confirm:
                        console.print("[yellow]Update cancelled.[/yellow]")
                        return

                binary_path = get_executable_path()
                if not update_asset(repo, assets[0], binary_path):
                    console.print(f"[green]Already using the latest version ({current_version})[/green]")
                else:
                    console.print(f"[green]Successfully updated to {latest_version}[/green]")
                return

        except Exception as e:
            console.print(f"[red]Unexpected error during update: {e}[/red]")
            console.print("\nFalling back to manual update instructions...")
            console.print("\nTo update, run:")
            console.print("\nOn Mac or Linux, run:")
            console.print(f"[bold cyan]curl -LsSf {ENV.HCLI_RELEASE_URL}/install | sh[/bold cyan]")
            console.print("\nOr on Windows, run:")
            console.print(f"[bold cyan]iwr {ENV.HCLI_RELEASE_URL}/install.ps1 | iex[/bold cyan]")
            raise click.Abort()

    else:
        console.print("\nTo update, run:")
        console.print("[bold cyan]uv tool upgrade ida-hcli[/bold cyan]")
        console.print("or")
        console.print("[bold cyan]pipx upgrade ida-hcli[/bold cyan]")
