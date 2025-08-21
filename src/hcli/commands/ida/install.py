from __future__ import annotations

from pathlib import Path
from typing import Optional

import rich_click as click

from hcli.commands.download import download
from hcli.commands.license.get import get_license
from hcli.lib.auth import get_auth_service
from hcli.lib.commands import async_command, enforce_login
from hcli.lib.console import console
from hcli.lib.ida import accept_eula, get_ida_path, install_ida, install_license
from hcli.lib.util.io import get_temp_dir


@click.option("-d", "--download-id", "download_slug", required=False, help="Installer slug")
@click.option("-l", "--license-id", "license_id", required=False, help="License pubhash")
@click.option("-i", "--install-dir", "install_dir", required=True, help="Install dir")
@click.option("-a", "--accept-eula", "eula", is_flag=True, help="Accept EULA", default=True)
@click.argument("installer", required=False)
@click.command()
@click.pass_context
@async_command
async def install(
    ctx, install_dir: str, eula: bool, installer: str, download_slug: Optional[str], license_id: Optional[str]
) -> None:
    """Installs IDA unattended.

    If install_dir is /tmp/myida, the ida binary will be located:

    \b
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
            await download.callback(output_dir=tmp_dir, slug=download_slug)
            # Find the downloaded installer file
            installer_path = Path(tmp_dir) / Path(download_slug).name
            installer = str(installer_path)

        # Download the file
        console.print(f"[yellow]Installing {installer}...[/yellow]")
        await install_ida(installer, install_dir)

        if eula:
            console.print("[yellow]Accepting EULA...[/yellow]")
            accept_eula(get_ida_path(install_dir))

        if license_id:
            # Call get_license command with the license ID
            await get_license.callback(lid=license_id, output_dir=tmp_dir)

            # Find a file *{license_id}.hexlic in tmp_dir
            license_files = list(Path(tmp_dir).glob(f"*{license_id}.hexlic"))
            if not license_files:
                raise FileNotFoundError(f"License file matching *{license_id}.hexlic not found in {tmp_dir}")
            license_file = license_files[0].name

            # Copy license file to install dir
            await install_license(str(Path(tmp_dir) / license_file), install_dir)

        console.print("[green]Installation complete![/green]")

    except Exception as e:
        console.print(f"[red]Download failed: {e}[/red]")
        raise
