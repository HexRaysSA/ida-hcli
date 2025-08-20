from __future__ import annotations

import rich_click as click

from hcli import __version__
from hcli.env import ENV
from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.update.updater import UpdateError, get_default_updater
from hcli.lib.update.version import (
    compare_versions,
    get_latest_version,
    is_binary,
)


@click.command()
@click.option("-f", "--force", is_flag=True, help="Force update.")
@click.option(
    "-m",
    "--mode",
    default="auto",
    type=click.Choice(["auto", "pypi", "binary"]),
    help="Update source (auto detects based on installation type).",
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
async def update(force: bool, mode: str, check_only: bool, auto_install: bool, include_prereleases: bool) -> None:
    """Check for updates to the CLI."""

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
            updater = get_default_updater()

            if auto_install:
                # Automatically install if update available
                updated = await updater.update_if_available(
                    include_prereleases=include_prereleases, restart_after_update=True
                )
                if not updated:
                    console.print(f"[green]Already using the latest version ({__version__})[/green]")
                return
            else:
                # Check for updates and offer installation
                release = await updater.check_for_updates(include_prereleases, force_check=check_only)
                if release:
                    console.print("[green]Update available![/green]")
                    console.print(f"Current version: [yellow]{__version__}[/yellow]")
                    console.print(f"Latest version: [green]{release.tag_name}[/green]")

                    if not check_only:
                        if click.confirm("Install update now?", default=True):
                            await updater.perform_update(release, restart_after_update=True)
                            return
                        else:
                            console.print("\nTo update later, run:")
                            console.print("[bold cyan]hcli update --auto-install[/bold cyan]")
                else:
                    console.print(f"[green]Already using the latest version ({__version__})[/green]")
                return

        except UpdateError as e:
            console.print(f"[red]Update failed: {e}[/red]")
            console.print("\nFalling back to manual update instructions...")
        except Exception as e:
            console.print(f"[red]Unexpected error during update: {e}[/red]")
            console.print("\nFalling back to manual update instructions...")

    # Original update check logic for non-GitHub or non-auto-install modes
    latest_version = await get_latest_version("ida-hcli", mode, include_prereleases)
    current_version = __version__

    if not latest_version:
        console.print("[red]Could not fetch latest version information[/red]")
        return

    update_available = compare_versions(current_version, latest_version)

    if update_available:
        console.print("[green]Update available![/green]")
        console.print(f"Current version: [yellow]{current_version}[/yellow]")
        console.print(f"Latest version: [green]{latest_version}[/green]")

        if not check_only:
            if mode == "freeze":
                console.print("\nFor binary updates, run:")
                console.print("[bold cyan]hcli update --auto-install[/bold cyan]")
            elif not is_binary():
                console.print("\nTo update, run:")
                console.print("\nOn Mac or Linux, run:")
                console.print(f"[bold cyan]curl -LsSf {ENV.HCLI_RELEASE_URL}/install | sh[/bold cyan]")
                console.print("\nOr on Windows, run:")
                console.print(f"[bold cyan]iwr {ENV.HCLI_RELEASE_URL}/install.ps1 | iex[/bold cyan]")
            else:
                console.print("\nTo update, run:")
                console.print("[bold cyan]uv tool upgrade ida-hcli[/bold cyan]")
                console.print("or")
                console.print("[bold cyan]pipx upgrade ida-hcli[/bold cyan]")

    else:
        console.print(f"[green]You are using the latest version ({current_version})[/green]")
