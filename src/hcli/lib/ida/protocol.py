from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from hcli.lib.console import console
from hcli.lib.util.io import get_hcli_command

PROTOCOL = "ida"

# Strip PYTHON* vars first: the requesting app (e.g. IDA running its own Python) leaks
# PYTHONHOME into the handler's environment, which would make hcli's pinned interpreter
# load the wrong stdlib and abort before it runs. env -u clears them.
_PY_ENV_STRIP = "env -u PYTHONHOME -u PYTHONPATH -u PYTHONEXECUTABLE -u PYTHONSTARTUP"


def _macos_handler_applescript(hcli_cmd: str, log_file: str, log_dir: str) -> str:
    """Build the AppleScript handler body.

    ``hcli_cmd`` is the hcli invocation already rendered as a POSIX shell fragment
    (``shlex.join`` of the argv) — its tokens are individually quoted, so a spaced
    install path survives the zsh ``-c`` parse as one word. ``ida open -- <url>``:
    the ``--`` stops the URL (or anything that decodes from it) from being parsed as
    an option to the open command. The handler ``mkdir -p``s the log dir at click
    time so a removed/uncreatable dir can't break the redirect.
    """
    return f'''
on open location this_URL
    set logFile to "{log_file}"
    set logDir to "{log_dir}"
    do shell script "/bin/mkdir -p " & quoted form of logDir & " ; /bin/zsh -l -c " & quoted form of ("{_PY_ENV_STRIP} {hcli_cmd} ida open -- " & quoted form of this_URL) & " >> " & quoted form of logFile & " 2>&1"
end open location

on run
    -- This handler is called when the app is launched directly
end run
'''


def _linux_desktop_entry(hcli_cmd: str) -> str:
    """Build the .desktop entry.

    ``hcli_cmd`` is the hcli invocation already rendered as a shell fragment
    (``shlex.join`` of the argv), so a spaced install path stays a single token when
    the desktop environment parses ``Exec=``. ``-- %u`` keeps the URL from being
    parsed as an option.
    """
    return f"""[Desktop Entry]
Name=HCLI IDB Link Handler
Exec={_PY_ENV_STRIP} {hcli_cmd} ida open -- %u
Type=Application
NoDisplay=true
MimeType=x-scheme-handler/{PROTOCOL};
"""


def setup_macos_protocol_handler() -> None:
    """Set up protocol handler for macOS using AppleScript and plist modification."""
    try:
        # shlex.join quotes each argv token for the zsh -c parse, so a spaced
        # install path stays one word instead of being split into bogus arguments.
        hcli_cmd = shlex.join(get_hcli_command())

        # Create AppleScript application that handles ida:// URLs
        # Use login shell (-l) to get full user environment, avoiding sandbox restrictions.
        # Strip PYTHON* vars first: the requesting app (e.g. IDA running its own
        # Python 3.13) leaks PYTHONHOME into the handler's environment, which makes
        # hcli's pinned interpreter load the wrong stdlib and abort ("No module named
        # 'encodings'") before it runs. env -u clears them so hcli uses its own Python.
        # Log to a per-user, non-world-writable location. A fixed /tmp path is a
        # classic symlink-write target on shared machines (another user pre-plants a
        # symlink and the handler's ">>" appends into a file they chose), and the log
        # records full ida:// URLs.
        log_dir = Path.home() / "Library" / "Logs"
        # Best-effort at registration; the handler also `mkdir -p`s at click time, so a
        # locked-down home (or the dir being removed later) can't fail registration or
        # break the redirect.
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        log_file = str(log_dir / "idb_handler.log")
        applescript_content = _macos_handler_applescript(hcli_cmd, log_file, str(log_dir))

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

            # Add URL scheme handler and LSUIElement (to hide from Dock) to plist
            url_scheme_xml = f"""
        <key>CFBundleURLTypes</key>
        <array>
            <dict>
                <key>CFBundleURLName</key>
                <string>IDB URL Handler</string>
                <key>CFBundleURLSchemes</key>
                <array>
                    <string>{PROTOCOL}</string>
                </array>
            </dict>
        </array>
        <key>LSUIElement</key>
        <true/>"""

            # Insert before closing </dict></plist>
            if "<key>CFBundleURLTypes</key>" not in plist_content:
                plist_content = plist_content.replace("</dict>\n</plist>", f"{url_scheme_xml}\n</dict>\n</plist>")

                # Write back the modified plist
                with tempfile.NamedTemporaryFile(
                    encoding="utf-8", mode="w", suffix=".plist", delete=False
                ) as temp_plist:
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

        hcli_argv = get_hcli_command()

        # Register ida:// protocol.
        # A PYTHONHOME/PYTHONPATH leaked by the launching app (e.g. IDA running its own
        # Python) would point a non-frozen hcli's interpreter at the wrong stdlib and
        # abort it before it runs. Neutralize it WITHOUT cmd.exe: routing the
        # percent-encoded URL through `cmd /c` corrupts its %XX escapes, and there is no
        # `env -u` on Windows. ShellExecute/CreateProcess passes argv verbatim, so invoke
        # the interpreter directly with -E (ignore all PYTHON* env vars). Frozen builds
        # embed their own runtime and are immune, so run them as-is.
        # Build the command with subprocess.list2cmdline: it applies the exact
        # CommandLineToArgvW quoting Windows uses to split this REG_SZ back into argv,
        # so a spaced install path stays a single argument.
        # Use a "--" separator before %1: Windows substitutes the raw URL into the
        # command line, and a stray quote in the URL could otherwise append extra
        # argv tokens parsed as options to `ida open`. After "--" click treats the
        # URL strictly as the positional argument; injected tokens become surplus
        # positionals and are rejected rather than interpreted. "%1" stays a literal
        # placeholder (we quote it ourselves so a spaced URL arrives as one argument).
        if getattr(sys, "frozen", False):
            command = subprocess.list2cmdline([*hcli_argv, "ida", "open", "--"]) + ' "%1"'
        else:
            command = f'"{sys.executable}" -E -m hcli.main ida open -- "%1"'
        reg_key = rf"SOFTWARE\Classes\{PROTOCOL}"

        with winreg.CreateKey(HKEY_CURRENT_USER, reg_key) as key:  # type: ignore[attr-defined]
            winreg.SetValueEx(key, "", 0, REG_SZ, f"URL:{PROTOCOL.upper()} Protocol")  # type: ignore[attr-defined]
            winreg.SetValueEx(key, "URL Protocol", 0, REG_SZ, "")  # type: ignore[attr-defined]

        # Icon comes from the launcher executable (argv[0]); quote it for spaced paths.
        with winreg.CreateKey(HKEY_CURRENT_USER, rf"{reg_key}\DefaultIcon") as key:  # type: ignore[attr-defined]
            winreg.SetValueEx(key, "", 0, REG_SZ, f'"{hcli_argv[0]}",1')  # type: ignore[attr-defined]

        with winreg.CreateKey(HKEY_CURRENT_USER, rf"{reg_key}\shell") as key:  # type: ignore[attr-defined]
            pass

        with winreg.CreateKey(HKEY_CURRENT_USER, rf"{reg_key}\shell\open") as key:  # type: ignore[attr-defined]
            pass

        with winreg.CreateKey(HKEY_CURRENT_USER, rf"{reg_key}\shell\open\command") as key:  # type: ignore[attr-defined]
            winreg.SetValueEx(key, "", 0, REG_SZ, command)  # type: ignore[attr-defined]

        console.print(f"[green]✓[/green] Windows protocol handler ({PROTOCOL}://) registered in registry")

    except ImportError:
        console.print("[red]winreg module not available (not on Windows?)[/red]")
        raise
    except Exception as e:
        console.print(f"[red]Error setting up Windows protocol handler: {e}[/red]")
        raise


def setup_linux_protocol_handler() -> None:
    """Set up protocol handler for Linux using desktop entry and xdg-mime."""
    try:
        # shlex.join quotes each argv token so a spaced install path stays one word
        # when the desktop environment parses the Exec= line.
        hcli_cmd = shlex.join(get_hcli_command())

        # Write to applications directory
        applications_dir = Path.home() / ".local" / "share" / "applications"
        applications_dir.mkdir(parents=True, exist_ok=True)

        # The desktop launcher passes %u as a literal argv, so no shell mangles the URL.
        desktop_content = _linux_desktop_entry(hcli_cmd)

        desktop_path = applications_dir / "hcli-idb-handler.desktop"
        desktop_path.write_text(desktop_content)
        desktop_path.chmod(0o755)

        # Register with xdg-mime
        subprocess.run(["xdg-mime", "default", "hcli-idb-handler.desktop", f"x-scheme-handler/{PROTOCOL}"], check=True)

        # Update desktop database
        subprocess.run(
            ["update-desktop-database", str(applications_dir)], check=False
        )  # May fail on some systems but not critical

        console.print("[green]✓[/green] Linux protocol handler installed:")
        console.print(f"    {PROTOCOL}:// -> {desktop_path}")

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

        reg_key = rf"SOFTWARE\Classes\{PROTOCOL}"
        removed = False

        try:
            winreg.DeleteKeyEx(HKEY_CURRENT_USER, rf"{reg_key}\shell\open\command")  # type: ignore[attr-defined]
            winreg.DeleteKeyEx(HKEY_CURRENT_USER, rf"{reg_key}\shell\open")  # type: ignore[attr-defined]
            winreg.DeleteKeyEx(HKEY_CURRENT_USER, rf"{reg_key}\shell")  # type: ignore[attr-defined]
            winreg.DeleteKeyEx(HKEY_CURRENT_USER, rf"{reg_key}\DefaultIcon")  # type: ignore[attr-defined]
            winreg.DeleteKeyEx(HKEY_CURRENT_USER, reg_key)  # type: ignore[attr-defined]
            removed = True
        except FileNotFoundError:
            pass

        if removed:
            console.print(f"[green]✓[/green] Windows protocol handler ({PROTOCOL}://) removed from registry")
        else:
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
        desktop_path = applications_dir / "hcli-idb-handler.desktop"

        if not desktop_path.exists():
            console.print("[yellow]Linux protocol handler not found (already removed)[/yellow]")
            return

        desktop_path.unlink()

        # Remove mime association
        subprocess.run(
            ["xdg-mime", "default", "", f"x-scheme-handler/{PROTOCOL}"],
            check=False,
        )

        # Update desktop database
        subprocess.run(
            ["update-desktop-database", str(applications_dir)],
            check=False,
        )

        console.print(f"[green]✓[/green] Linux protocol handler ({PROTOCOL}://) removed")

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
