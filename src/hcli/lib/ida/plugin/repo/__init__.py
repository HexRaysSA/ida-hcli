import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass

from hcli.lib.ida.plugin import (
    discover_platforms_from_plugin_archive,
    get_metadatas_with_paths_from_plugin_archive,
    validate_metadata_in_plugin_archive,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginArchiveLocation:
    url: str
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


class PluginArchiveIndex:
    """index a collection of plugin archive URLs by name/version/idaVersion/platform.

    Plugins are uniquely identified by the name.
    There may be multiple versions of a plugin.
    Each version may have multiple distribution archives due to:
      - different IDA versions (compiled against SDK for 7.4 versus 9.2)
      - different platforms (compiled for Windows, macOS, Linux)
    """

    def __init__(self):
        # name -> version -> tuple[idaVersion, set[platforms]] -> list[url]
        self.index: dict[str, dict[str, dict[tuple[str, frozenset[str]], list[str]]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(list))
        )

    def index_plugin_archive(self, buf: bytes, url: str):
        for _, metadata in get_metadatas_with_paths_from_plugin_archive(buf):
            try:
                validate_metadata_in_plugin_archive(buf, metadata)
            except ValueError:
                return

            name = metadata.name
            version = metadata.version
            ida_versions = metadata.ida_versions or ">=0"
            platforms: frozenset[str] = discover_platforms_from_plugin_archive(buf, name)
            spec = (ida_versions, platforms)

            versions = self.index[name]
            specs = versions[version]
            specs[spec].append(url)
            logger.debug("found plugin: %s %s IDA:%s %s %s", name, version, ida_versions, platforms, url)

    def get_plugins(self) -> list[Plugin]:
        ret = []
        for name, versions in self.index.items():
            locations_by_version = defaultdict(list)
            for version, specs in versions.items():
                for spec, urls in specs.items():
                    ida_versions, platforms = spec
                    for url in urls:
                        location = PluginArchiveLocation(url, name, version, ida_versions, platforms)
                        locations_by_version[version].append(location)

            plugin = Plugin(name, locations_by_version)
            ret.append(plugin)

        return ret
