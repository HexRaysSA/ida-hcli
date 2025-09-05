from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

import rich_click as click

from hcli.lib.commands import async_command
from hcli.lib.config import config_store
from hcli.lib.console import console
from hcli.lib.ida import add_instance_to_config, find_standard_installations, generate_instance_name, is_ida_dir
from hcli.lib.util.io import get_hcli_executable_path


def setup_macos_protocol_handler() -> None:
    """Set up protocol handler for macOS using AppleScript and plist modification."""
    try:
        hcli_path = get_hcli_executable_path()

        print(hcli_path)

        # Create AppleScript application
        applescript_content = f'''
on open location this_URL
    do shell script "{hcli_path} ke open " & quoted form of this_URL
end open location

on run
    -- This handler is called when the app is launched directly
end run
'''

        # Create temporary directory for the AppleScript
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "HCLIHandler.applescript"
            app_path = Path.home() / "Applications" / "HCLIHandler.app"

            # Write AppleScript
            script_path.write_text(applescript_content)

            # Compile AppleScript to application
            subprocess.run(["osacompile", "-o", str(app_path), str(script_path)], check=True)

            # Create Info.plist for the app to register URL scheme
            info_plist_path = app_path / "Contents" / "Info.plist"

            # Read existing plist
            result = subprocess.run(
                ["plutil", "-convert", "xml1", "-o", "-", str(info_plist_path)],
                capture_output=True,
                text=True,
                check=True,
            )

            plist_content = result.stdout

            # Add URL scheme handler to plist
            url_scheme_xml = """
        <key>CFBundleURLTypes</key>
        <array>
            <dict>
                <key>CFBundleURLName</key>
                <string>IDA URL Handler</string>
                <key>CFBundleURLSchemes</key>
                <array>
                    <string>ida</string>
                </array>
            </dict>
        </array>"""

            # Insert before closing </dict></plist>
            if "<key>CFBundleURLTypes</key>" not in plist_content:
                plist_content = plist_content.replace("</dict>\n</plist>", f"{url_scheme_xml}\n</dict>\n</plist>")

                # Write back the modified plist
                with tempfile.NamedTemporaryFile(mode="w", suffix=".plist", delete=False) as temp_plist:
                    temp_plist.write(plist_content)
                    temp_plist_path = temp_plist.name

                # Convert back to binary and replace original
                subprocess.run(["plutil", "-convert", "binary1", temp_plist_path], check=True)

                shutil.copy2(temp_plist_path, info_plist_path)
                os.unlink(temp_plist_path)

            # Register the app with Launch Services
            subprocess.run(
                [
                    "/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister",
                    "-f",
                    str(app_path),
                ],
                check=True,
            )

            console.print(f"[green]✓[/green] macOS protocol handler installed at {app_path}")

    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to set up macOS protocol handler: {e}[/red]")
        raise
    except Exception as e:
        console.print(f"[red]Error setting up macOS protocol handler: {e}[/red]")
        raise


def setup_windows_protocol_handler() -> None:
    """Set up protocol handler for Windows using registry entries."""
    try:
        import winreg  # type: ignore[import-untyped]
        from winreg import HKEY_CURRENT_USER, REG_SZ  # type: ignore[import-untyped,attr-defined]

        hcli_path = get_hcli_executable_path()
        command = f'"{hcli_path}" ke open "%1"'

        # Create registry entries for ida:// protocol
        with winreg.CreateKey(HKEY_CURRENT_USER, r"SOFTWARE\Classes\ida") as key:  # type: ignore[attr-defined]
            winreg.SetValueEx(key, "", 0, REG_SZ, "URL:IDA Protocol")  # type: ignore[attr-defined]
            winreg.SetValueEx(key, "URL Protocol", 0, REG_SZ, "")  # type: ignore[attr-defined]

        with winreg.CreateKey(HKEY_CURRENT_USER, r"SOFTWARE\Classes\ida\DefaultIcon") as key:  # type: ignore[attr-defined]
            winreg.SetValueEx(key, "", 0, REG_SZ, f"{hcli_path},1")  # type: ignore[attr-defined]

        with winreg.CreateKey(HKEY_CURRENT_USER, r"SOFTWARE\Classes\ida\shell") as key:  # type: ignore[attr-defined]
            pass

        with winreg.CreateKey(HKEY_CURRENT_USER, r"SOFTWARE\Classes\ida\shell\open") as key:  # type: ignore[attr-defined]
            pass

        with winreg.CreateKey(HKEY_CURRENT_USER, r"SOFTWARE\Classes\ida\shell\open\command") as key:  # type: ignore[attr-defined]
            winreg.SetValueEx(key, "", 0, REG_SZ, command)  # type: ignore[attr-defined]

        console.print("[green]✓[/green] Windows protocol handler registered in registry")

    except ImportError:
        console.print("[red]winreg module not available (not on Windows?)[/red]")
        raise
    except Exception as e:
        console.print(f"[red]Error setting up Windows protocol handler: {e}[/red]")
        raise


def setup_linux_protocol_handler() -> None:
    """Set up protocol handler for Linux using desktop entry and xdg-mime."""
    try:
        hcli_path = get_hcli_executable_path()

        # Create desktop entry
        desktop_entry_content = f"""[Desktop Entry]
Name=HCLI URL Handler
Exec={hcli_path} ke open %u
Type=Application
NoDisplay=true
MimeType=x-scheme-handler/ida;
"""

        # Write to applications directory
        applications_dir = Path.home() / ".local" / "share" / "applications"
        applications_dir.mkdir(parents=True, exist_ok=True)

        desktop_file_path = applications_dir / "hcli-url-handler.desktop"
        desktop_file_path.write_text(desktop_entry_content)

        # Make executable
        desktop_file_path.chmod(0o755)

        # Register with xdg-mime
        subprocess.run(["xdg-mime", "default", "hcli-url-handler.desktop", "x-scheme-handler/ida"], check=True)

        # Update desktop database
        subprocess.run(
            ["update-desktop-database", str(applications_dir)], check=False
        )  # May fail on some systems but not critical

        console.print(f"[green]✓[/green] Linux protocol handler installed at {desktop_file_path}")

    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to set up Linux protocol handler: {e}[/red]")
        raise
    except Exception as e:
        console.print(f"[red]Error setting up Linux protocol handler: {e}[/red]")
        raise


@click.command(name="setup")
@click.option("--force", is_flag=True, help="Force reinstall even if already configured")
@async_command
async def setup(force: bool = False) -> None:
    """Set up hcli protocol handlers for ida:// URLs.

    This command registers hcli as the handler for ida:// URLs on your system,
    allowing web browsers and other applications to automatically open IDA-related
    URLs with hcli.

    The setup process varies by platform:
    - macOS: Creates an AppleScript application and registers it with Launch Services
    - Windows: Adds registry entries for the ida:// protocol
    - Linux: Creates a desktop entry and registers with xdg-mime
    """
    current_platform = platform.system().lower()

    console.print(f"[blue]Setting up hcli protocol handlers for {current_platform}...[/blue]")

    try:
        if current_platform == "darwin":
            setup_macos_protocol_handler()
        elif current_platform == "windows":
            setup_windows_protocol_handler()
        elif current_platform == "linux":
            setup_linux_protocol_handler()
        else:
            console.print(f"[red]Unsupported platform: {current_platform}[/red]")
            raise RuntimeError(f"Platform {current_platform} is not supported")

        console.print("[green]✓ Protocol handler setup complete![/green]")
        console.print("[yellow]You can now click ida:// links and they will open with hcli.[/yellow]")

        # Check if IDA instances are registered
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

            # Set the first one as default if no default exists
            first_instance = generate_instance_name(valid_installations[0])
            config_store.set_string("ke.ida.default", first_instance)
            console.print(f"[green]✓ Set '{first_instance}' as default IDA instance[/green]")
        else:
            console.print("[yellow]! All discovered IDA instances were already registered[/yellow]")

    except Exception as e:
        console.print(f"[yellow]! Could not auto-discover IDA installations: {e}[/yellow]")
        _print_ida_setup_instructions()


def _print_ida_setup_instructions() -> None:
    """Print instructions for manually setting up IDA instances."""
    console.print("\n[yellow]To use ida:// links, you need to register IDA Pro instances:[/yellow]")
    console.print("  • Auto-discover: [cyan]hcli ke ida add --auto[/cyan]")
    console.print("  • Manual: [cyan]hcli ke ida add <name> <path>[/cyan]")
    console.print("  • Example: [cyan]hcli ke ida add ida-pro '/Applications/IDA Professional 9.2.app'[/cyan]")
    console.print("  • Set default: [cyan]hcli ke ida switch <name>[/cyan]")
