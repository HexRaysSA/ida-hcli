from __future__ import annotations

import json
from pathlib import Path

import rich_click as click
from rich.prompt import Confirm

from hcli.commands.download import download
from hcli.commands.license.get import get_license
from hcli.lib.auth import get_auth_service
from hcli.lib.commands import async_command, enforce_login
from hcli.lib.console import console
from hcli.lib.ida import (
    IdaProduct,
    accept_eula,
    get_default_ida_install_directory,
    get_ida_config_path,
    get_ida_path,
    get_license_dir,
    install_ida,
    install_license,
)
from hcli.lib.util.io import get_os, get_temp_dir


@click.option(
    "-d",
    "--download-id",
    "download_id",
    required=False,
    help="Full installer asset key, or tag (e.g., 'ida-pro:latest', 'ida-essential:9.2')",
)
@click.option("-l", "--license-id", "license_id", required=False, help="License ID (e.g., 96-0000-0000-01)")
@click.option("-i", "--install-dir", "install_dir", required=False, help="Install dir")
@click.option("-a", "--accept-eula", "eula", is_flag=True, help="Accept EULA", default=True)
@click.option("--set-default", is_flag=True, help="Mark this IDA installation as the default", default=False)
@click.option("--dry-run", is_flag=True, help="Show what would be done without actually installing")
@click.option("--yes", "-y", "auto_confirm", is_flag=True, help="Auto-accept confirmation prompts", default=False)
@click.argument("installer", required=False)
@click.command()
@click.pass_context
@async_command
async def install(
    ctx,
    install_dir: str | None,
    eula: bool,
    installer: str,
    download_id: str | None,
    license_id: str | None,
    set_default: bool,
    dry_run: bool,
    auto_confirm: bool,
) -> None:
    """Installs IDA unattended.

    The --download-id option supports tags for simplified version specification:
    - 'category:version' (e.g., 'ida-pro:latest') - OS is auto-detected
    - 'category:version:os' (e.g., 'ida-pro:9.2:x64linux') - explicit OS

    If install_dir is /tmp/myida, the ida binary will be located:

    On Windows: /tmp/myida/ida
    On Linux: /tmp/myida/ida
    On Mac: /tmp/myida/Contents/MacOS/ida
    """
    try:
        tmp_dir = get_temp_dir()

        if download_id or license_id:
            auth_service = get_auth_service()
            auth_service.init()

            enforce_login()

        # download installer using the download command
        if download_id:
            # Download the installer (may be a tag like 'ida-pro:latest' or a full key)
            await download.callback(output_dir=tmp_dir, key=download_id)

            # Find the downloaded installer file in tmp_dir
            # Look for common IDA installer extensions
            installer_files: list[Path] = []
            for pattern in ["*.app.zip", "*.run", "*.exe", "*.zip"]:
                installer_files.extend(Path(tmp_dir).glob(pattern))

            if not installer_files:
                raise FileNotFoundError(f"No installer file found in {tmp_dir} after download")

            # Use the most recently modified file (should be the one we just downloaded)
            installer_path = max(installer_files, key=lambda p: p.stat().st_mtime).resolve()
        elif installer is not None:
            installer_path = Path(installer).resolve()
        else:
            raise click.UsageError("Either provide an installer file path or use --download-id to download one")

        if not installer_path.exists():
            raise FileNotFoundError(
                f"Installer file not found: {installer_path}\nPlease ensure the file exists at the specified location."
            )

        version = IdaProduct.from_installer_filename(installer_path.name)

        if not install_dir:
            install_dir_path = get_default_ida_install_directory(version)
        else:
            install_dir_path = Path(install_dir).expanduser().resolve()

        # prominent warning for #99: idat from IDA 9.2 on Linux fails to start if the path contains a space.
        #
        # typically idat isn't widely used; however, HCLI does use it to discover the path to IDA's Python interpreter,
        # as well as the installed arch (ARM or Intel on macOS). The latter could probably be discovered by inspecting
        # the installed files; however, figuring out the Python configuration is messy, and much easier to leave to idat.
        #
        # see also the special handling in hcli.lib.ida
        if get_os() == "linux" and version.major == 9 and version.minor == 2 and install_dir:
            if " " in str(install_dir_path.absolute()):
                console.print(
                    "[yellow]Warning[/yellow]: Avoid installation paths with a space for this release (IDA 9.2 on Linux).\n",
                    "This specific release of IDA has a bug in idat, preventing it from working\n",
                    "when the path contains a space.\n\n",
                    "You can find alternative workarounds in #99.\n\n",
                    f"[grey69]Current directory name:[/grey69] '{install_dir_path}'",
                )

                if not auto_confirm:
                    if not Confirm.ask("[bold yellow]Continue anyway?[/bold yellow]", default=False):
                        console.print("[yellow]Installation cancelled.[/yellow]")
                        return

        console.print("\n[bold]Installation details:[/bold]")
        console.print(f"  Installer: {installer_path}")
        console.print(f"  Destination: {install_dir_path}")
        if license_id:
            console.print(f"  License: {license_id}")
        if set_default:
            console.print("  Set as default: Yes")

        if dry_run:
            console.print("\n[bold cyan]Dry run mode - no changes will be made[/bold cyan]")
            console.print("\n[bold]Would perform the following actions:[/bold]")
            console.print(f"  1. Extract installer to: {install_dir_path}")
            if license_id:
                license_dir_path = get_license_dir(install_dir_path)
                console.print(f"  2. Install license to: {license_dir_path}")
            if set_default:
                config_path = get_ida_config_path()
                console.print(f"  3. Update default IDA path in: {config_path}")
            if eula:
                console.print("  4. Accept EULA")
            return

        if not auto_confirm and not Confirm.ask("\n[bold yellow]Proceed with installation?[/bold yellow]"):
            console.print("[yellow]Installation cancelled.[/yellow]")
            return

        console.print(f"[yellow]Installing {installer_path} to {install_dir_path}...[/yellow]")

        install_ida(installer_path, install_dir_path)

        if license_id:
            await get_license.callback(lid=license_id, output_dir=tmp_dir)

            # Find a file *{license_id}.hexlic in tmp_dir
            license_files = list(Path(tmp_dir).glob(f"*{license_id}.hexlic"))
            if not license_files:
                raise FileNotFoundError(f"License file matching *{license_id}.hexlic not found in {tmp_dir}")
            license_file = license_files[0].name

            license_dir_path = get_license_dir(install_dir_path)

            install_license(Path(tmp_dir) / license_file, license_dir_path)

        if set_default:
            config_path = get_ida_config_path()
            if not config_path.exists():
                console.print("[yellow]Updating configuration (default installation)...[/yellow]")
                config_path.parent.mkdir(parents=True, exist_ok=True)
                _ = config_path.write_text(json.dumps({"Paths": {"ida-install-dir": str(install_dir_path.absolute())}}))
                console.print("[grey69]Wrote default ida-config.json[/grey69]")
            else:
                # we update this without Pydantic validation to ensure we always can make the changes
                # and leave config validation to the code that requires interpretation of the file.
                doc = json.loads(config_path.read_text(encoding="utf-8"))
                if "Paths" not in doc:
                    doc["Paths"] = {}
                existing = doc["Paths"].get("ida-install-dir") or "(empty)"
                new = str(install_dir_path.absolute())
                doc["Paths"]["ida-install-dir"] = new
                _ = config_path.write_text(json.dumps(doc), encoding="utf-8")
                console.print("[grey69]Updated ida-config.json:[/grey69]")
                console.print(f"[grey69]  default install path: {existing}[/grey69]")
                console.print(f"[grey69]                     -> {new}[/grey69]")

        # this requires using ida_registry to set some keys
        # which requires idalib to be working
        # so it has to go after license and config installation
        if eula and version.product:
            if version.product in ("IDA Free", "IDA Home", "IDA Classroom"):
                # these products don't include idalib, which is used to write to the registry.
                console.print("[yellow]Skipped EULA acceptance due to product features.[/yellow]")
            else:
                # maybe its safer to have an allow-list for products with idalib
                console.print("[yellow]Accepting EULA...[/yellow]")
                try:
                    accept_eula(get_ida_path(install_dir_path))
                except RuntimeError:
                    console.print("[red]Skipped EULA acceptance due to missing idalib.[/red]")

        console.print("[green]Installation complete![/green]")

    except Exception as e:
        console.print(f"[red]Install failed: {e}[/red]")
        raise
