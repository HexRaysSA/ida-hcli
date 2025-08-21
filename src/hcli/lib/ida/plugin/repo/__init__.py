from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PluginVersion:
    version: str
    url: str


@dataclass
class Plugin:
    name: str
    versions: list[PluginVersion]


class BasePluginRepo(ABC):
    @abstractmethod
    def get_plugins(self) -> list[Plugin]: ...
