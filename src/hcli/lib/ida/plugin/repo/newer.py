"""A repository view without the installed and older versions of one plugin."""

from __future__ import annotations

import semantic_version

from hcli.lib.ida.plugin import parse_plugin_version
from hcli.lib.ida.plugin.reference import normalize_plugin_host
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin, PluginArchiveLocation


class NewerVersionsRepo(BasePluginRepo):
    """A repository view in which one plugin has only the versions newer than its installed version.

    The resolver then never checks the compatibility of the installed or older versions, so an
    upgrade with nothing newer does not probe IDA's Python.
    """

    def __init__(self, inner: BasePluginRepo, name: str, host: str, installed_version: str) -> None:
        self._inner = inner
        self._name = name.lower()
        self._host = normalize_plugin_host(host)
        self._installed_version = parse_plugin_version(installed_version)
        self._plugins: list[Plugin] | None = None

    def get_plugins(self) -> list[Plugin]:
        if self._plugins is None:
            self._plugins = [self._filter(plugin) for plugin in self._inner.get_plugins()]
        return self._plugins

    def has_newer_versions(self, version_spec: str) -> bool:
        """Check whether a version newer than the installed version matches `version_spec`, such as `>=1.0`, or empty for any version."""
        spec = semantic_version.SimpleSpec(version_spec or ">=0")
        return any(
            parse_plugin_version(version) in spec
            for plugin in self.get_plugins()
            if self._is_upgraded_plugin(plugin)
            for version in plugin.versions
        )

    def get_older_version(self, version_spec: str) -> str | None:
        """Get the newest version that matches `version_spec` and is older than the installed version."""
        spec = semantic_version.SimpleSpec(version_spec or ">=0")
        versions = [
            version
            for plugin in self._inner.get_plugins()
            if self._is_upgraded_plugin(plugin)
            for version in plugin.versions
            if parse_plugin_version(version) in spec and parse_plugin_version(version) < self._installed_version
        ]
        return max(versions, key=parse_plugin_version, default=None)

    def _is_upgraded_plugin(self, plugin: Plugin) -> bool:
        return plugin.name.lower() == self._name and normalize_plugin_host(plugin.host) == self._host

    def _filter(self, plugin: Plugin) -> Plugin:
        if not self._is_upgraded_plugin(plugin):
            return plugin
        versions = {
            version: locations
            for version, locations in plugin.versions.items()
            if parse_plugin_version(version) > self._installed_version
        }
        return plugin.model_copy(update={"versions": versions})

    def _fetch_and_verify(self, location: PluginArchiveLocation) -> tuple[str, bytes]:
        return self._inner._fetch_and_verify(location)
