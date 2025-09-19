from __future__ import annotations

import platform

import rich_click as click

from hcli.lib.commands import async_command
from hcli.lib.config import config_store
from hcli.lib.console import console
from hcli.lib.ida import add_instance_to_config, find_standard_installations, generate_instance_name, is_ida_dir
from hcli.lib.ida.protocol import register_protocol_handler, unregister_protocol_handler


@click.command(name="setup")
@click.option("--force", is_flag=True, help="Force reinstall even if already configured")
@click.option("--unregister", is_flag=True, help="Remove URL handlers instead of installing them")
@async_command
async def setup(force: bool = False, unregister: bool = False) -> None:
    """Set up or remove hcli protocol handlers for ida:// URLs.

    This command registers hcli as the handler for ida:// URLs on your system,
    allowing web browsers and other applications to automatically open IDA-related
    URLs with hcli. Use --unregister to remove the protocol handlers.

    The setup/removal process varies by platform:
    - macOS: Creates/removes an AppleScript application and registers/unregisters with Launch Services
    - Windows: Adds/removes registry entries for the ida:// protocol
    - Linux: Creates/removes a desktop entry and registers/unregisters with xdg-mime
    """
    current_platform = platform.system().lower()

    if unregister:
        console.print(f"[blue]Removing hcli protocol handlers for {current_platform}...[/blue]")

        try:
            unregister_protocol_handler()

            console.print("[green]✓ Protocol handler removal complete![/green]")
            console.print("[yellow]ida:// links will no longer open with hcli.[/yellow]")

        except Exception as e:
            console.print(f"[red]Unregistration failed: {e}[/red]")
            raise
    else:
        console.print(f"[blue]Setting up hcli protocol handlers for {current_platform}...[/blue]")

        try:
            register_protocol_handler()

            console.print("[green]✓ Protocol handler setup complete![/green]")
            console.print("[yellow]You can now click ida:// links and they will open with hcli.[/yellow]")

            # Check if IDA instances are registered (only during setup, not unregistration)
            await _check_and_setup_ida_instances()

        except Exception as e:
            console.print(f"[red]Setup failed: {e}[/red]")
            raise


async def _check_and_setup_ida_instances() -> None:
    """Check for registered IDA instances and auto-discover if none exist."""
    # Check if any IDA instances are already registered
    instances: dict[str, str] = config_store.get_object("ke.ida.instances", {}) or {}

    if instances:
        console.print(f"[green]✓ Found {len(instances)} registered IDA instance(s)[/green]")
        default_instance = config_store.get_string("ke.ida.default", "")
        if default_instance:
            console.print(f"[green]✓ Default IDA instance: {default_instance}[/green]")
        else:
            console.print("[yellow]! No default IDA instance set. Use 'hcli ke ida switch' to set one.[/yellow]")
        return

    console.print("\n[blue]Checking for IDA Pro installations...[/blue]")

    # Try to auto-discover IDA installations
    try:
        installations = find_standard_installations()
        valid_installations = [inst for inst in installations if is_ida_dir(inst)]

        if not valid_installations:
            console.print("[yellow]! No IDA Pro installations found.[/yellow]")
            _print_ida_setup_instructions()
            return

        console.print(f"[green]✓ Found {len(valid_installations)} IDA installation(s)[/green]")

        # Auto-register the discovered installations
        added_count = 0
        for installation in valid_installations:
            instance_name = generate_instance_name(installation)
            if add_instance_to_config(instance_name, installation):
                added_count += 1

        if added_count > 0:
            console.print(f"[green]✓ Automatically registered {added_count} IDA instance(s)[/green]")

            # Set the last one alphabetically as default if no default exists
            sorted_installations = sorted(valid_installations, key=lambda p: generate_instance_name(p))
            last_instance = generate_instance_name(sorted_installations[-1])
            config_store.set_string("ke.ida.default", last_instance)
            console.print(f"[green]✓ Set '{last_instance}' as default IDA instance[/green]")
        else:
            console.print("[yellow]! All discovered IDA instances were already registered[/yellow]")

    except Exception as e:
        console.print(f"[yellow]! Could not auto-discover IDA installations: {e}[/yellow]")
        _print_ida_setup_instructions()


@click.command(name="install")
@async_command
async def install() -> None:
    """Install the KE plugin."""
    console.print("Coming soon...")


def _print_ida_setup_instructions() -> None:
    """Print instructions for manually setting up IDA instances."""
    console.print("\n[yellow]To use ida:// links, you need to register IDA Pro instances:[/yellow]")
    console.print("  • Auto-discover: [cyan]hcli ke ida add --auto[/cyan]")
    console.print("  • Manual: [cyan]hcli ke ida add <name> <path>[/cyan]")
    console.print("  • Example: [cyan]hcli ke ida add ida-pro '/Applications/IDA Professional 9.2.app'[/cyan]")
    console.print("  • Set default: [cyan]hcli ke ida switch <name>[/cyan]")
