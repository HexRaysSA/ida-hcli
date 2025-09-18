import hashlib
import logging
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import requests
import semantic_version
from pydantic import BaseModel, ConfigDict, field_serializer

from hcli.lib.ida.plugin import (
    IDAMetadataDescriptor,
    get_metadatas_with_paths_from_plugin_archive,
    is_ida_version_compatible,
    parse_plugin_version,
    validate_metadata_in_plugin_archive,
)

logger = logging.getLogger(__name__)


def fetch_plugin_archive(url: str) -> bytes:
    parsed_url = urlparse(url)

    if parsed_url.scheme == "file":
        file_path = Path(parsed_url.path)
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
    name: str
    version: str
    ida_versions: str
    platforms: frozenset[str]
    metadata: IDAMetadataDescriptor

    @field_serializer("platforms")
    def serialize_platforms_in_order(self, value: frozenset[str]):
        return sorted(value)


class Plugin(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)  # type: ignore

    name: str
    # version -> list[PluginVersion]
    versions: dict[str, list[PluginArchiveLocation]]


class BasePluginRepo(ABC):
    @abstractmethod
    def get_plugins(self) -> list[Plugin]: ...

    def find_compatible_plugin_from_spec(
        self, plugin_spec: str, current_platform: str, current_version: str
    ) -> PluginArchiveLocation:
        plugin_name: str = re.split("[=><!~/]", plugin_spec)[0]
        wanted_spec = semantic_version.SimpleSpec(plugin_spec[len(plugin_name) :] or ">=0")

        plugins = [plugin for plugin in self.get_plugins() if plugin.name == plugin_name]
        if not plugins:
            raise ValueError(f"plugin not found: {plugin_name}")
        if len(plugins) > 1:
            raise RuntimeError("too many plugins found")

        plugin: Plugin = plugins[0]

        versions = reversed(sorted(plugin.versions.keys(), key=parse_plugin_version))
        for version in versions:
            version_spec = parse_plugin_version(version)
            if version_spec not in wanted_spec:
                logger.debug("skipping: %s not in %s", version_spec, wanted_spec)
                continue

            logger.debug("found matching version: %s", version)
            for i, location in enumerate(plugin.versions[version]):
                if current_platform not in location.platforms:
                    logger.debug(
                        "skipping location %d: unsupported platforms: %s",
                        i,
                        location.platforms,
                    )
                    continue

                if not is_ida_version_compatible(current_version, location.ida_versions):
                    logger.debug(
                        "skipping location %d: unsupported IDA versions: %s",
                        i,
                        location.ida_versions,
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

    Plugins are uniquely identified by the name.
    There may be multiple versions of a plugin.
    Each version may have multiple distribution archives due to:
      - different IDA versions (compiled against SDK for 7.4 versus 9.2)
      - different platforms (compiled for Windows, macOS, Linux)
    """

    def __init__(self):
        # name -> version -> tuple[idaVersion, set[platforms]] -> list[tuple[url, sha256]]
        self.index: dict[
            str, dict[str, dict[tuple[str, frozenset[str]], list[tuple[str, str, IDAMetadataDescriptor]]]]
        ] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    def index_plugin_archive(self, buf: bytes, url: str):
        for _, metadata in get_metadatas_with_paths_from_plugin_archive(buf):
            try:
                validate_metadata_in_plugin_archive(buf, metadata)
            except ValueError:
                return

            h = hashlib.sha256()
            h.update(buf)
            sha256 = h.hexdigest()

            name = metadata.plugin.name
            version = metadata.plugin.version
            ida_versions = metadata.plugin.ida_versions or ">=0"
            platforms = frozenset(metadata.plugin.platforms)
            spec = (ida_versions, platforms)

            versions = self.index[name]
            specs = versions[version]
            specs[spec].append((url, sha256, metadata))
            logger.debug(
                "found plugin: %s %s IDA:%s %s %s",
                name,
                version,
                ida_versions,
                platforms,
                url,
            )

    def get_plugins(self) -> list[Plugin]:
        """
        Fetch all plugins and their locations, indexed by name/version/ida version/platforms.
        The results are stably sorted.
        """
        ret = []

        # sort alphabetically by name
        for name, versions in sorted(self.index.items(), key=lambda p: p[0]):
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
                            version=version,
                            ida_versions=ida_versions,
                            platforms=platforms,
                            metadata=metadata,
                        )
                        locations_by_version[version].append(location)

            plugin = Plugin(name=name, versions=locations_by_version)
            ret.append(plugin)

        return ret
