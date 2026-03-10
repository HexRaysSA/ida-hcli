from __future__ import annotations

import os
import platform
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import rich_click as click

from hcli.lib.api.asset import asset as asset_api
from hcli.lib.api.common import get_api_client
from hcli.lib.commands import async_command, auth_command
from hcli.lib.config import config_store
from hcli.lib.console import console

from .common import KNOWN_TOOLS, fetch_tool_assets


def _get_default_tool_install_dir() -> Path:
    """Return the default directory for installing tools."""
    if platform.system() == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        return Path(local_app_data) / "hex-rays" / "hcli" / "tools"
    return Path.home() / ".local" / "bin"


async def _resolve_tool(name: str, version: str | None) -> tuple[str, str, str] | None:
    """Resolve a tool name (and optional version) to an asset key.

    Returns (asset_key, tool_name, resolved_version) or None on failure.
    """
    # Validate name against known tools (case-insensitive)
    matched_known = None
    for known in KNOWN_TOOLS:
        if known.lower() == name.lower():
            matched_known = known
            break

    if not matched_known:
        console.print(f"[red]Unknown tool: '{name}'[/red]")
        console.print("[yellow]Known tools:[/yellow]")
        for t in sorted(KNOWN_TOOLS):
            console.print(f"  - {t}")
        return None

    assets = await fetch_tool_assets()

    # Filter to matching tool name
    matches = [(k, n, v) for k, n, v in assets if n.lower() == matched_known.lower()]

    if not matches:
        console.print(f"[red]No assets found for tool '{matched_known}'[/red]")
        return None

    if version:
        # Match specific version
        for asset_key, tool_name, ver in matches:
            if ver == version:
                return (asset_key, tool_name, ver)
        available = sorted({v for _, _, v in matches}, reverse=True)
        console.print(f"[red]Version '{version}' not found for '{matched_known}'[/red]")
        console.print(f"[yellow]Available versions: {', '.join(available)}[/yellow]")
        return None

    # No version specified: pick the highest (lexicographic sort)
    matches.sort(key=lambda x: x[2], reverse=True)
    return matches[0]


@auth_command()
@click.argument("download_id")
@click.option("--install-dir", type=click.Path(), default=None, help="Custom install directory")
@click.option("--force", is_flag=True, help="Reinstall even if already installed")
@async_command
async def install_tool(download_id: str, install_dir: str | None, force: bool) -> None:
    """Install an IDA-related utility tool.

    DOWNLOAD_ID: Tool name (e.g., 'vault2git') or name:version (e.g., 'vault2git:9.4')
    """
    # Parse name[:version]
    if ":" in download_id:
        name, version = download_id.split(":", 1)
        version = None if version == "latest" else version
    else:
        name, version = download_id, None

    result = await _resolve_tool(name, version)
    if not result:
        return
    asset_key, tool_name, version = result

    console.print(f"[green]Resolved: {tool_name} v{version} → {asset_key}[/green]")

    # Check if already installed
    installed: dict = config_store.get_object("tools.installed", {}) or {}
    if tool_name in installed and not force:
        existing = installed[tool_name]
        console.print(
            f"[yellow]{tool_name} is already installed (version: {existing.get('version', 'unknown')})[/yellow]"
        )
        console.print("[yellow]Use --force to reinstall[/yellow]")
        return

    # Determine install directory
    target_dir = Path(install_dir) if install_dir else _get_default_tool_install_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    # Download to temp dir
    console.print(f"[yellow]Downloading {tool_name}...[/yellow]")
    with tempfile.TemporaryDirectory(prefix="hcli_tool_") as tmp_dir:
        client = await get_api_client()
        asset = await asset_api.get_file("installers", asset_key)
        if not asset or not asset.url:
            console.print(f"[red]Failed to get download URL for {asset_key}[/red]")
            return

        downloaded_path = await client.download_file(
            asset.url, target_dir=tmp_dir, force=True, auth=True, asset_key=asset_key
        )

        # Determine target filename
        binary_name = tool_name
        if platform.system() == "Windows":
            binary_name += ".exe"

        target_path = target_dir / binary_name

        # Move downloaded file to install dir
        shutil.move(str(downloaded_path), str(target_path))

        # Make executable on Unix
        if platform.system() != "Windows":
            current_stat = os.stat(target_path)
            os.chmod(target_path, current_stat.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Update config
    installed[tool_name] = {
        "version": version,
        "key": asset_key,
        "path": str(target_path),
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    config_store.set_object("tools.installed", installed)

    console.print(f"[green]Successfully installed {tool_name} to {target_path}[/green]")

    # Check PATH
    if not shutil.which(binary_name):
        console.print(f"[yellow]Warning: {target_dir} is not in your PATH[/yellow]")
        console.print(f"[yellow]Add it to your PATH to use '{binary_name}' directly[/yellow]")
