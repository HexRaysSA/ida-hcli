from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from hcli.lib.console import console
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
    except Exception:
        console.print("[red]Error setting up Windows protocol handler: {e}[/red]")
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


def unregister_macos_protocol_handler() -> None:
    """Remove protocol handler for macOS by deleting the AppleScript application."""
    try:
        app_path = Path.home() / "Applications" / "HCLIHandler.app"

        if not app_path.exists():
            console.print("[yellow]macOS protocol handler not found (already removed)[/yellow]")
            return

        # Remove the application
        shutil.rmtree(app_path)

        # Unregister from Launch Services
        subprocess.run(
            [
                "/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister",
                "-u",
                str(app_path),
            ],
            check=False,  # Don't fail if app is already gone
        )

        console.print(f"[green]✓[/green] macOS protocol handler removed from {app_path}")

    except Exception as e:
        console.print(f"[red]Error removing macOS protocol handler: {e}[/red]")
        raise


def unregister_windows_protocol_handler() -> None:
    """Remove protocol handler for Windows by deleting registry entries."""
    try:
        import winreg  # type: ignore[import-untyped]
        from winreg import HKEY_CURRENT_USER  # type: ignore[import-untyped,attr-defined]

        try:
            # Delete the registry key
            winreg.DeleteKeyEx(HKEY_CURRENT_USER, r"SOFTWARE\Classes\ida\shell\open\command")  # type: ignore[attr-defined]
            winreg.DeleteKeyEx(HKEY_CURRENT_USER, r"SOFTWARE\Classes\ida\shell\open")  # type: ignore[attr-defined]
            winreg.DeleteKeyEx(HKEY_CURRENT_USER, r"SOFTWARE\Classes\ida\shell")  # type: ignore[attr-defined]
            winreg.DeleteKeyEx(HKEY_CURRENT_USER, r"SOFTWARE\Classes\ida\DefaultIcon")  # type: ignore[attr-defined]
            winreg.DeleteKeyEx(HKEY_CURRENT_USER, r"SOFTWARE\Classes\ida")  # type: ignore[attr-defined]

            console.print("[green]✓[/green] Windows protocol handler removed from registry")

        except FileNotFoundError:
            console.print("[yellow]Windows protocol handler not found (already removed)[/yellow]")

    except ImportError:
        console.print("[red]winreg module not available (not on Windows?)[/red]")
        raise
    except Exception as e:
        console.print(f"[red]Error removing Windows protocol handler: {e}[/red]")
        raise


def unregister_linux_protocol_handler() -> None:
    """Remove protocol handler for Linux by deleting desktop entry and mime associations."""
    try:
        applications_dir = Path.home() / ".local" / "share" / "applications"
        desktop_file_path = applications_dir / "hcli-url-handler.desktop"

        if not desktop_file_path.exists():
            console.print("[yellow]Linux protocol handler not found (already removed)[/yellow]")
            return

        # Remove the desktop file
        desktop_file_path.unlink()

        # Remove mime association
        subprocess.run(
            ["xdg-mime", "default", "", "x-scheme-handler/ida"],
            check=False,  # Don't fail if mime association doesn't exist
        )

        # Update desktop database
        subprocess.run(
            ["update-desktop-database", str(applications_dir)],
            check=False,  # May fail on some systems but not critical
        )

        console.print(f"[green]✓[/green] Linux protocol handler removed from {desktop_file_path}")

    except Exception as e:
        console.print(f"[red]Error removing Linux protocol handler: {e}[/red]")
        raise


def register_protocol_handler() -> None:
    """Set up protocol handler for the current platform."""
    current_platform = platform.system().lower()

    if current_platform == "darwin":
        setup_macos_protocol_handler()
    elif current_platform == "windows":
        setup_windows_protocol_handler()
    elif current_platform == "linux":
        setup_linux_protocol_handler()
    else:
        console.print(f"[red]Unsupported platform: {current_platform}[/red]")
        raise RuntimeError(f"Platform {current_platform} is not supported")


def unregister_protocol_handler() -> None:
    """Remove protocol handler for the current platform."""
    current_platform = platform.system().lower()

    if current_platform == "darwin":
        unregister_macos_protocol_handler()
    elif current_platform == "windows":
        unregister_windows_protocol_handler()
    elif current_platform == "linux":
        unregister_linux_protocol_handler()
    else:
        console.print(f"[red]Unsupported platform: {current_platform}[/red]")
        raise RuntimeError(f"Platform {current_platform} is not supported")
