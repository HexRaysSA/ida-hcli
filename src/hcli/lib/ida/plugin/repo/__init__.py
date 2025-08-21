from abc import ABC, abstractmethod
from dataclasses import dataclass


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
