"""Plugin install command."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import requests
import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida import find_current_ida_platform, find_current_ida_version
from hcli.lib.ida.plugin import get_metadata_from_plugin_archive, get_metadatas_with_paths_from_plugin_archive
from hcli.lib.ida.plugin.install import install_plugin_archive

logger = logging.getLogger(__name__)


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
def install_plugin(ctx, plugin: str) -> None:
    plugin_spec = plugin
    try:
        current_platform = find_current_ida_platform()
        current_ida_version = find_current_ida_version()

        if Path(plugin_spec).exists() and plugin_spec.endswith(".zip"):
            logger.info("installing from the local file system")
            buf = Path(plugin_spec).read_bytes()
            items = list(get_metadatas_with_paths_from_plugin_archive(buf))
            if len(items) != 1:
                raise ValueError("plugin archive must contain a single plugin for local file system installation")
            plugin_name = items[0][1].name

        elif plugin_spec.startswith("file://"):
            logger.info("installing from the local file system")
            # fetch from file system
            buf = fetch_plugin_archive(plugin_spec)
            items = list(get_metadatas_with_paths_from_plugin_archive(buf))
            if len(items) != 1:
                raise ValueError("plugin archive must contain a single plugin for local file system installation")
            plugin_name = items[0][1].name

        elif plugin_spec.startswith("https://"):
            logger.info("installing from HTTP URL")
            buf = fetch_plugin_archive(plugin_spec)
            items = list(get_metadatas_with_paths_from_plugin_archive(buf))
            if len(items) != 1:
                raise ValueError("plugin archive must contain a single plugin for HTTP URL installation")
            plugin_name = items[0][1].name

        else:
            logger.info("finding plugin in repository")
            plugin_name = re.split("=><!~", plugin_spec)[0]
            plugin_repo = ctx.obj["plugin_repo"]
            location = plugin_repo.find_compatible_plugin_from_spec(plugin_spec, current_platform, current_ida_version)
            buf = fetch_plugin_archive(location.url)

        install_plugin_archive(buf, plugin_name)

        metadata = get_metadata_from_plugin_archive(buf, plugin_name)

        console.print(f"[green]Installed[/green] plugin: [blue]{plugin_name}[/blue]=={metadata.version}")
    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
