"""Plugin install command."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import requests
import rich_click as click
import semantic_version

from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.ida import find_current_ida_platform, find_current_ida_version
from hcli.lib.ida.plugin import get_metadata_from_plugin_archive, get_metadatas_with_paths_from_plugin_archive, parse_plugin_version
from hcli.lib.ida.plugin.install import install_plugin_archive, is_ida_version_compatible
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin

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


def find_compatible_plugin_from_spec(
    plugin_repo: BasePluginRepo, plugin_spec: str, current_platform: str, current_version: str
) -> bytes:
    plugin_name: str = re.split("=><!~", plugin_spec)[0]
    wanted_spec = semantic_version.SimpleSpec(plugin_spec[len(plugin_name):] or ">=0")

    plugins = [plugin for plugin in plugin_repo.get_plugins() if plugin.name == plugin_name]
    if not plugins:
        raise ValueError(f"plugin not found: {plugin_name}")
    if len(plugins) > 1:
        raise RuntimeError("too many plugins found")

    plugin: Plugin = plugins[0]

    versions = reversed(sorted(plugin.locations_by_version.keys(), key=parse_plugin_version))
    for version in versions:
        version_spec = parse_plugin_version(version)
        if version_spec not in wanted_spec:
            logger.debug("skipping: %s not in %s", version_spec, wanted_spec)
            continue

        logger.debug("found matching version: %s", version)
        for i, location in enumerate(plugin.locations_by_version[version]):
            if current_platform not in location.platforms:
                logger.debug("skipping location %d: unsupported platforms: %s", i, location.platforms)
                continue

            if not is_ida_version_compatible(current_version, location.ida_versions):
                logger.debug("skipping location %d: unsupported IDA versions: %s", i, location.ida_versions)
                continue

            return fetch_plugin_archive(location.url)

    raise KeyError("failed to find compatible plugin")


@click.command()
@click.pass_context
@click.argument("plugin")
@async_command
async def install_plugin(ctx, plugin: str) -> None:
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
            buf = find_compatible_plugin_from_spec(
                ctx.obj["plugin_repo"], plugin_spec, current_platform, current_ida_version
            )

        install_plugin_archive(buf, plugin_name)

        metadata = get_metadata_from_plugin_archive(buf, plugin_name)

        console.print(f"[green]Installed[/green] plugin: [blue]{plugin_name}[/blue]=={metadata.version}")
    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
