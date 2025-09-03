import hashlib
import logging
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass

import semantic_version

from hcli.lib.ida.plugin import (
    discover_platforms_from_plugin_archive,
    get_metadatas_with_paths_from_plugin_archive,
    is_ida_version_compatible,
    parse_plugin_version,
    validate_metadata_in_plugin_archive,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginArchiveLocation:
    url: str
    sha256: str
    name: str
    version: str
    ida_versions: str
    platforms: frozenset[str]


@dataclass
class Plugin:
    name: str
    # version -> list[PluginVersion]
    locations_by_version: dict[str, list[PluginArchiveLocation]]


class BasePluginRepo(ABC):
    @abstractmethod
    def get_plugins(self) -> list[Plugin]: ...

    def find_compatible_plugin_from_spec(
        self, plugin_spec: str, current_platform: str, current_version: str
    ) -> PluginArchiveLocation:
        plugin_name: str = re.split("=><!~", plugin_spec)[0]
        wanted_spec = semantic_version.SimpleSpec(plugin_spec[len(plugin_name) :] or ">=0")

        plugins = [plugin for plugin in self.get_plugins() if plugin.name == plugin_name]
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
        self.index: dict[str, dict[str, dict[tuple[str, frozenset[str]], list[tuple[str, str]]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(list))
        )

    def index_plugin_archive(self, buf: bytes, url: str):
        for _, metadata in get_metadatas_with_paths_from_plugin_archive(buf):
            try:
                validate_metadata_in_plugin_archive(buf, metadata)
            except ValueError:
                return

            h = hashlib.sha256()
            h.update(buf)
            sha256 = h.hexdigest()

            name = metadata.name
            version = metadata.version
            ida_versions = metadata.ida_versions or ">=0"
            platforms: frozenset[str] = discover_platforms_from_plugin_archive(buf, name)
            spec = (ida_versions, platforms)

            versions = self.index[name]
            specs = versions[version]
            specs[spec].append((url, sha256))
            logger.debug(
                "found plugin: %s %s IDA:%s %s %s",
                name,
                version,
                ida_versions,
                platforms,
                url,
            )

    def get_plugins(self) -> list[Plugin]:
        ret = []
        for name, versions in self.index.items():
            locations_by_version = defaultdict(list)
            for version, specs in versions.items():
                for spec, urls in specs.items():
                    ida_versions, platforms = spec
                    for url, sha256 in urls:
                        location = PluginArchiveLocation(
                            url=url,
                            sha256=sha256,
                            name=name,
                            version=version,
                            ida_versions=ida_versions,
                            platforms=platforms,
                        )
                        locations_by_version[version].append(location)

            plugin = Plugin(name, locations_by_version)
            ret.append(plugin)

        return ret
