"""Plugin install command."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

import requests
import rich_click as click
from packaging.version import Version
from semantic_version import SimpleSpec

from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.ida import find_current_ida_platform, find_current_ida_version
from hcli.lib.ida.plugin.install import install_plugin_archive

logger = logging.getLogger(__name__)


def _is_ida_version_compatible(current_version: str, version_spec: str) -> bool:
    """Check if current IDA version is compatible with the version specifier.

    Args:
        current_version: Current IDA version (e.g., "9.1")
        version_spec: Version specifier (e.g., ">=8.0", "~=9.0", ">=0")

    Returns:
        True if current version satisfies the specifier
    """
    try:
        current = Version(current_version)
        spec = SimpleSpec(version_spec)
        return current in spec
    except Exception as e:
        logger.debug(f"Error checking version compatibility: {e}")
        return False


def fetch_plugin_archive(url: str) -> bytes:
    parsed_url = urlparse(url)

    if parsed_url.scheme == "file":
        # Handle file:// URLs
        file_path = Path(parsed_url.path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        return file_path.read_bytes()

    elif parsed_url.scheme in ("http", "https"):
        # Handle HTTP(S) URLs
        response = requests.get(url, timeout=30.0)
        response.raise_for_status()
        return response.content

    else:
        raise ValueError(f"Unsupported URL scheme: {parsed_url.scheme}")


@click.command()
@click.pass_context
@click.argument("plugin")
@async_command
async def install_plugin(ctx, plugin_spec: str) -> None:
    try:
        plugin_repo = ctx.obj["plugin_repo"]

        plugin_name, _, wanted_version = plugin_spec.partition("==")

        plugins = plugin_repo.get_plugins()

        current_platform = find_current_ida_platform()
        current_ida_version = find_current_ida_version()

        for plugin in sorted(plugins, key=lambda p: p.name):
            if plugin.name != plugin_name:
                continue

            for version, locations in plugin.locations_by_version.items():
                for location in locations:
                    if wanted_version and wanted_version != version:
                        logger.debug(
                            f"Skipping plugin {plugin.name} version {version} because it is not the wanted version {wanted_version}"
                        )
                        continue

                    if current_platform not in location.platforms:
                        logger.debug(
                            f"Skipping plugin {plugin.name} version {version} because it is not compatible with the current platform {current_platform}"
                        )
                        continue

                    if not _is_ida_version_compatible(current_ida_version, location.ida_versions):
                        logger.debug(
                            f"Skipping plugin {plugin.name} version {version} because IDA version {current_ida_version} is not compatible with {location.ida_versions}"
                        )
                        continue

                    # fetch data, using https:// prefix or file:/// prefix
                    buf = fetch_plugin_archive(location.url)
                    install_plugin_archive(buf, plugin_name)

        if not plugins:
            console.print("[grey69]No plugins found[/grey69]")

    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
