"""A plugin repository spanning several named repositories.

Repositories are fetched independently and merged here, in the client, so the
name a plugin was found under is always known -- unlike a server-side merge,
which would hand back one anonymous document.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from hcli.lib.ida import HEXRAYS_REPO_NAME, PluginRepository
from hcli.lib.ida.plugin.exceptions import PluginAccessDeniedError
from hcli.lib.ida.plugin.repo import PLUGIN_REPO_HOST, BasePluginRepo, Plugin
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo

logger = logging.getLogger(__name__)


def _describe_failure(name: str, error: Exception) -> str:
    if isinstance(error, PluginAccessDeniedError):
        if not error.authenticated:
            return f"{name}: not logged in"
        return f"{name}: {'credentials rejected' if error.status_code == 401 else 'not entitled'}"
    if isinstance(error, (httpx.ConnectError, httpx.TimeoutException)):
        return f"{name}: unreachable"
    if isinstance(error, httpx.HTTPStatusError):
        return f"{name}: HTTP {error.response.status_code}"
    return f"{name}: {error}"


def _identity_host(plugin: Plugin) -> str:
    return (urlparse(plugin.host).hostname or "").lower()


class AggregatePluginRepo(BasePluginRepo):
    """Several named repositories presented as one.

    Callers pass the scope they want -- one repository, or all of them. This
    class holds no notion of a default: which repository resolves an unprefixed
    reference is a configuration question, answered by the caller.
    """

    def __init__(self, repositories: dict[str, PluginRepository]):
        super().__init__()
        self.repositories = repositories
        self._plugins: dict[str, list[Plugin]] = {}
        self._failures: dict[str, Exception] = {}
        self._dropped: dict[str, int] = {}
        # Which repository served which plugin, remembered at load time rather
        # than reconstructed later by scanning every loaded list.
        self._owner: dict[int, str] = {}

    def _load(self, name: str) -> list[Plugin]:
        """Fetch one repository, at most once, remembering failure as well as success."""
        if name in self._plugins:
            return self._plugins[name]
        if name in self._failures:
            raise self._failures[name]

        repo = self.repositories[name]
        try:
            plugins = JSONFilePluginRepo.from_url(repo.url, repo_name=name).get_plugins()
        except Exception as e:
            logger.debug("failed to fetch plugin repository %s (%s): %s", name, repo.url, e)
            self._failures[name] = e
            raise

        kept = self._filter_entitled(name, plugins)
        self._plugins[name] = kept
        for plugin in kept:
            self._owner[id(plugin)] = name
        return kept

    def _filter_entitled(self, name: str, plugins: list[Plugin]) -> list[Plugin]:
        """Drop plugins claiming an identity this repository may not assert.

        Recorded rather than printed: a repository that could not fully answer
        is reported through notes(), the same way an unreachable one is, so
        --json callers see it too instead of only text-mode users.
        """
        if name == HEXRAYS_REPO_NAME:
            return plugins

        kept = [p for p in plugins if _identity_host(p) != PLUGIN_REPO_HOST]
        dropped = len(plugins) - len(kept)
        if dropped:
            logger.debug("repository %s served %d plugin(s) claiming Hex-Rays identities", name, dropped)
            self._dropped[name] = dropped
        return kept

    def notes(self) -> list[str]:
        """What the caller should know about repositories consulted so far.

        Covers both a repository that could not be reached and one that was
        reached but served plugins it is not entitled to. Both mean the answer
        is not the whole picture, so both belong in the same report.
        """
        notes = []
        for name in self.repositories:
            if name in self._failures:
                notes.append(f"skipped -- {_describe_failure(name, self._failures[name])}")
            if name in self._dropped:
                notes.append(f"{name}: ignored {self._dropped[name]} plugin(s) claiming Hex-Rays identities")
        return notes

    def get_plugins_in(self, name: str) -> list[Plugin]:
        """Plugins from one named repository. Propagates that repository's failure."""
        if name not in self.repositories:
            raise KeyError(f"unknown plugin repository: {name}")
        return list(self._load(name))

    def get_plugins(self) -> list[Plugin]:
        """Every plugin hcli can see, across every configured repository.

        Best-effort by design: one unreachable or unauthorized repository must
        not hide the results of the others, so failures are recorded and
        reported through notes() rather than raised.
        """
        plugins: list[Plugin] = []
        for name in self.repositories:
            try:
                plugins.extend(self._load(name))
            except Exception as e:
                logger.debug("skipping plugin repository %s: %s", name, e)
        return plugins

    def repo_of(self, plugin: Plugin) -> str | None:
        """Which loaded repository served this plugin."""
        return self._owner.get(id(plugin))
