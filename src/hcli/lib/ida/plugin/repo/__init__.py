import hashlib
import logging
import urllib.request
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import requests
import semantic_version
from pydantic import BaseModel, ConfigDict

from hcli.lib.ida.plugin import (
    IDAMetadataDescriptor,
    IdaVersion,
    Platform,
    get_metadatas_with_paths_from_plugin_archive,
    is_ida_version_compatible,
    parse_plugin_version,
    split_plugin_version_spec,
    validate_metadata_in_plugin_archive,
)

logger = logging.getLogger(__name__)


def fetch_plugin_archive(url: str) -> bytes:
    parsed_url = urlparse(url)

    if parsed_url.scheme == "file":
        file_path = Path(urllib.request.url2pathname(parsed_url.path))
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        return file_path.read_bytes()

    elif parsed_url.scheme in ("http", "https"):
        response = requests.get(url, timeout=30.0)
        response.raise_for_status()
        return response.content

    else:
        raise ValueError(f"Unsupported URL scheme: {parsed_url.scheme}")


class PluginArchiveLocation(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True, frozen=True)  # type: ignore

    url: str
    sha256: str
    metadata: IDAMetadataDescriptor


class Plugin(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)  # type: ignore

    name: str
    host: str  # repo URL (or other canonical URL in the future, when supported.)
    # version -> list[PluginVersion]
    versions: dict[str, list[PluginArchiveLocation]]


def is_compatible_plugin_version_location(
    plugin: Plugin, version: str, location: PluginArchiveLocation, current_platform: str, current_version: str
) -> bool:
    if not is_ida_version_compatible(current_version, location.metadata.plugin.ida_versions):
        return False

    if current_platform not in location.metadata.plugin.platforms:
        return False

    return True


def is_compatible_plugin_version(
    plugin: Plugin, version: str, locations: list[PluginArchiveLocation], current_platform: str, current_version: str
) -> bool:
    return any(
        is_compatible_plugin_version_location(plugin, version, location, current_platform, current_version)
        for location in locations
    )


def is_compatible_plugin(plugin: Plugin, current_platform: str, current_version: str) -> bool:
    return any(
        is_compatible_plugin_version(plugin, version, locations, current_platform, current_version)
        for version, locations in plugin.versions.items()
    )


def get_latest_plugin_metadata(plugin: Plugin) -> IDAMetadataDescriptor:
    max_version = max(plugin.versions.keys(), key=parse_plugin_version)
    max_locations = plugin.versions[max_version]
    return max_locations[0].metadata


def get_latest_compatible_plugin_metadata(
    plugin: Plugin, current_platform: str, current_version: str
) -> IDAMetadataDescriptor:
    for version, locations in sorted(plugin.versions.items(), key=lambda p: parse_plugin_version(p[0]), reverse=True):
        if is_compatible_plugin_version(plugin, version, locations, current_platform, current_version):
            return plugin.versions[version][0].metadata

    raise ValueError("no versions of plugin are compatible")


def get_plugin_by_name(plugins: list[Plugin], name: str, host: str | None = None) -> Plugin:
    if host:
        plugins = [
            plugin for plugin in plugins if plugin.name.lower() == name.lower() and plugin.host.lower() == host.lower()
        ]
    else:
        plugins = [plugin for plugin in plugins if plugin.name.lower() == name.lower()]

    if not plugins:
        raise KeyError(f"plugin not found: {name}")

    if len(plugins) > 1:
        logger.debug("found plugin:")
        for plugin in plugins:
            logger.debug("  - %s (%s)", plugin.name, plugin.host)

        # this needs to be implemented.
        # callers should handle this nicely and then provide host.
        # but nobody does this today.
        raise NotImplementedError(f"colliding plugin name: {name}")

    return plugins[0]


class BasePluginRepo(ABC):
    @abstractmethod
    def get_plugins(self) -> list[Plugin]: ...

    def get_plugin_by_name(self, name: str, host: str | None = None) -> Plugin:
        return get_plugin_by_name(self.get_plugins(), name, host=host)

    def find_compatible_plugin_from_spec(
        self, plugin_spec: str, current_platform: str, current_version: str, host: str | None = None
    ) -> PluginArchiveLocation:
        plugin_name, _ = split_plugin_version_spec(plugin_spec)
        wanted_spec = semantic_version.SimpleSpec(plugin_spec[len(plugin_name) :] or ">=0")

        plugin = self.get_plugin_by_name(plugin_name, host=host)

        versions = reversed(sorted(plugin.versions.keys(), key=parse_plugin_version))
        for version in versions:
            version_spec = parse_plugin_version(version)
            if version_spec not in wanted_spec:
                logger.debug("skipping: %s not in %s", version_spec, wanted_spec)
                continue

            logger.debug("found matching version: %s", version)
            for i, location in enumerate(plugin.versions[version]):
                if current_platform not in location.metadata.plugin.platforms:
                    logger.debug(
                        "skipping location %d: unsupported platforms: %s",
                        i,
                        location.metadata.plugin.platforms,
                    )
                    continue

                if not is_ida_version_compatible(current_version, location.metadata.plugin.ida_versions):
                    logger.debug(
                        "skipping location %d: unsupported IDA versions: %s",
                        i,
                        location.metadata.plugin.ida_versions,
                    )
                    continue

                return location

        raise KeyError(f"plugin not found: {plugin_spec}")

    def fetch_compatible_plugin_from_spec(self, plugin_spec: str, current_platform: str, current_version: str) -> bytes:
        location = self.find_compatible_plugin_from_spec(plugin_spec, current_platform, current_version)
        buf = fetch_plugin_archive(location.url)

        h = hashlib.sha256()
        h.update(buf)
        sha256 = h.hexdigest()

        if sha256 != location.sha256:
            raise ValueError(f"hash mismatch: expected {location.sha256} but found {sha256} for {location.url}")

        return buf


class PluginArchiveIndex:
    """index a collection of plugin archive URLs by name/version/idaVersion/platform.

    Plugins are uniquely identified by the name and host (repo URL today, xor other canonical URL in the future).
    There may be multiple versions of a plugin.
    Each version may have multiple distribution archives due to:
      - different IDA versions (compiled against SDK for 7.4 versus 9.2)
      - different platforms (compiled for Windows, macOS, Linux)
    """

    def __init__(self):
        # tuple[name, host] -> version -> tuple[set[IdaVersion], set[Platform]] -> list[tuple[url, sha256, metadata]]
        self.index: dict[
            tuple[str, str],
            dict[
                str,
                dict[tuple[frozenset[IdaVersion], frozenset[Platform]], list[tuple[str, str, IDAMetadataDescriptor]]],
            ],
        ] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    def index_plugin_archive(self, buf: bytes, url: str, expected_host: str | None = None):
        """Parse the given plugin archive and index the encountered plugins.

        Optionally filter out plugins whose host does not match the expected host.
        """
        for _, metadata in get_metadatas_with_paths_from_plugin_archive(buf):
            try:
                validate_metadata_in_plugin_archive(buf, metadata)
            except ValueError:
                return

            h = hashlib.sha256()
            h.update(buf)
            sha256 = h.hexdigest()

            name = metadata.plugin.name
            host = metadata.plugin.host
            version = metadata.plugin.version
            ida_versions = frozenset(metadata.plugin.ida_versions)
            platforms = frozenset(metadata.plugin.platforms)
            spec = (ida_versions, platforms)

            logger.debug(
                "found plugin: %s==%s, %d versions, for: %s, at: %s",
                name,
                version,
                len(ida_versions),
                platforms,
                url,
            )

            if expected_host and expected_host != host:
                logger.debug("%s: host mismatch: %s versus expected %s", name, host, expected_host)
                continue

            versions = self.index[(name, host)]
            specs = versions[version]
            specs[spec].append((url, sha256, metadata))

    def get_plugins(self) -> list[Plugin]:
        """
        Fetch all plugins and their locations, indexed by name/version/ida version/platforms.
        The results are stably sorted.
        """
        ret = []

        # sort alphabetically by name
        for id_, versions in sorted(self.index.items(), key=lambda p: p[0]):
            name, host = id_
            locations_by_version = defaultdict(list)

            # sort by version
            for version, specs in sorted(versions.items(), key=lambda p: parse_plugin_version(p[0])):
                # sorted arbitrarily (but stably)
                for spec, urls in sorted(specs.items()):
                    ida_versions, platforms = spec

                    # sorted arbitrarily (but stably)
                    for url, sha256, metadata in sorted(urls):
                        location = PluginArchiveLocation(
                            url=url,
                            sha256=sha256,
                            name=name,
                            host=host,
                            version=version,
                            ida_versions=ida_versions,
                            platforms=platforms,
                            metadata=metadata,
                        )
                        locations_by_version[version].append(location)

            plugin = Plugin(name=name, host=host, versions=locations_by_version)
            ret.append(plugin)

        return ret
