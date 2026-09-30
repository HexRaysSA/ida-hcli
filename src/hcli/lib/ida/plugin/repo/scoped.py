"""A read-once view of plugin repositories that takes some plugin names from one repository only."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from hcli.lib.ida.plugin.reference import normalize_plugin_host
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin, PluginArchiveLocation
from hcli.lib.ida.plugin.repo.aggregate import AggregatePluginRepo

logger = logging.getLogger(__name__)


class ScopedPluginRepo(BasePluginRepo):
    """The plugins of a repository, with some plugin names taken from a named repository only.

    `get_plugins()` reads the repositories one time. Plugins with the same name and host from
    different repositories are one plugin. Each archive is fetched one time, from the repository
    that listed it, which for an `AggregatePluginRepo` is the configured repository that served
    the plugin.
    """

    def __init__(self, inner: BasePluginRepo | None, named: Mapping[str, BasePluginRepo] | None = None) -> None:
        self._inner = inner
        self._named = {name.lower(): repo for name, repo in (named or {}).items()}
        self._plugins: list[Plugin] | None = None
        self._owners: dict[tuple[str, str], BasePluginRepo] = {}
        self._archives: dict[tuple[str, str], tuple[str, bytes]] = {}

    def get_plugins(self) -> list[Plugin]:
        if self._plugins is None:
            sources: list[tuple[BasePluginRepo, Plugin]] = []
            if self._inner is not None:
                sources.extend(
                    (self._inner, plugin)
                    for plugin in self._inner.get_plugins()
                    if plugin.name.lower() not in self._named
                )
            for name, repo in self._named.items():
                sources.extend((repo, plugin) for plugin in repo.get_plugins() if plugin.name.lower() == name)

            merged: dict[tuple[str, str], Plugin] = {}
            for repo, plugin in sources:
                owner = _get_owner(repo, plugin)
                key = (plugin.name.lower(), normalize_plugin_host(plugin.host))
                target = merged.setdefault(key, Plugin(name=plugin.name, host=plugin.host, versions={}))
                for version, locations in plugin.versions.items():
                    merged_locations = target.versions.setdefault(version, [])
                    for location in locations:
                        location_key = (location.url, location.sha256)
                        if location_key not in self._owners:
                            self._owners[location_key] = owner
                            merged_locations.append(location)
            self._plugins = list(merged.values())
        return self._plugins

    def _fetch_and_verify(self, location: PluginArchiveLocation) -> tuple[str, bytes]:
        key = (location.url, location.sha256)
        if key not in self._archives:
            self.get_plugins()
            owner = self._owners.get(key)
            if owner is None:
                self._archives[key] = super()._fetch_and_verify(location)
            else:
                self._archives[key] = owner._fetch_and_verify(location)
        return self._archives[key]


def _get_owner(repo: BasePluginRepo, plugin: Plugin) -> BasePluginRepo:
    if isinstance(repo, AggregatePluginRepo):
        name = repo.repo_of(plugin)
        if name is not None:
            return repo.get_child_repo(name)
    return repo
