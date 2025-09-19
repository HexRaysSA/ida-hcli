from __future__ import annotations

import json
from pathlib import Path

import rich_click as click

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
from hcli.lib.util.io import get_temp_dir


@click.option("-d", "--download-id", "download_slug", required=False, help="Installer slug")
@click.option("-l", "--license-id", "license_id", required=False, help="License ID (e.g., 48-307B-71D4-46)")
@click.option("-i", "--install-dir", "install_dir", required=False, help="Install dir")
@click.option("-a", "--accept-eula", "eula", is_flag=True, help="Accept EULA", default=True)
@click.option("--set-default", is_flag=True, help="Mark this IDA installation as the default", default=False)
@click.argument("installer", required=False)
@click.command()
@click.pass_context
@async_command
async def install(
    ctx,
    install_dir: str | None,
    eula: bool,
    installer: str,
    download_slug: str | None,
    license_id: str | None,
    set_default: bool,
) -> None:
    """Installs IDA unattended.

    If install_dir is /tmp/myida, the ida binary will be located:

    On Windows: /tmp/myida/ida
    On Linux: /tmp/myida/ida
    On Mac: /tmp/myida/Contents/MacOS/ida
    """
    try:
        # download installer using the download command
        tmp_dir = get_temp_dir()

        if download_slug or license_id:
            auth_service = get_auth_service()
            auth_service.init()

            # Enforce login
            enforce_login()

        if download_slug:
            await download.callback(output_dir=tmp_dir, key=download_slug)
            installer_path = Path(tmp_dir) / Path(download_slug).name
        else:
            installer_path = Path(installer)

        version = IdaProduct.from_installer_filename(installer_path.name)

        if not install_dir:
            install_dir_path = get_default_ida_install_directory(version)
        else:
            install_dir_path = Path(install_dir)

        console.print(f"[yellow]Installing {installer_path} to {install_dir_path}...[/yellow]")

        install_ida(installer_path, install_dir_path)

        if license_id:
            # Call get_license command with the license ID
            await get_license.callback(lid=license_id, output_dir=tmp_dir)

            # Find a file *{license_id}.hexlic in tmp_dir
            license_files = list(Path(tmp_dir).glob(f"*{license_id}.hexlic"))
            if not license_files:
                raise FileNotFoundError(f"License file matching *{license_id}.hexlic not found in {tmp_dir}")
            license_file = license_files[0].name

            license_dir_path = get_license_dir(install_dir_path)

            # Copy license file to install dir
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
        console.print(f"[red]Download failed: {e}[/red]")
        raise
