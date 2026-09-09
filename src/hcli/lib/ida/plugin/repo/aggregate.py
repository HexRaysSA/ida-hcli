"""A plugin repository spanning several named repositories.

Repositories are fetched independently and merged here, in the client, so the
name a plugin was found under is always known -- unlike a server-side merge,
which would hand back one anonymous document.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from hcli.lib.ida import PluginRepository
from hcli.lib.ida.plugin.exceptions import PluginAccessDeniedError
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo

logger = logging.getLogger(__name__)

# Identities under this host are Hex-Rays', and only the repository Hex-Rays
# serves may assert them. Adding a repository must not confer the power to
# impersonate us: without this, a repository the user added could claim
# "plugins.hex-rays.com/HexRaysSA/..." and, because `upgrade` resolves an
# installed plugin by host across every configured repository, take over
# upgrades of a genuinely installed private plugin.
PORTAL_IDENTITY_HOST = "plugins.hex-rays.com"
PORTAL_IDENTITY_REPO = "hexrays"


@dataclass(frozen=True)
class RepoFailure:
    """Why a repository could not be consulted."""

    name: str
    error: Exception

    def describe(self) -> str:
        e = self.error
        if isinstance(e, PluginAccessDeniedError):
            if not e.authenticated:
                return f"{self.name}: not logged in"
            return f"{self.name}: {'credentials rejected' if e.status_code == 401 else 'not entitled'}"
        if isinstance(e, (httpx.ConnectError, httpx.TimeoutException)):
            return f"{self.name}: unreachable"
        if isinstance(e, httpx.HTTPStatusError):
            return f"{self.name}: HTTP {e.response.status_code}"
        return f"{self.name}: {e}"


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
        self._failures: dict[str, RepoFailure] = {}

    def _load(self, name: str) -> list[Plugin]:
        """Fetch one repository, at most once, remembering failure as well as success."""
        if name in self._plugins:
            return self._plugins[name]
        if name in self._failures:
            raise self._failures[name].error

        repo = self.repositories[name]
        try:
            plugins = JSONFilePluginRepo.from_url(repo.url, repo_name=name).get_plugins()
        except Exception as e:
            logger.debug("failed to fetch plugin repository %s (%s): %s", name, repo.url, e)
            self._failures[name] = RepoFailure(name=name, error=e)
            raise

        self._plugins[name] = self._filter_entitled(name, plugins)
        return self._plugins[name]

    def _filter_entitled(self, name: str, plugins: list[Plugin]) -> list[Plugin]:
        """Drop plugins claiming an identity this repository may not assert."""
        if name == PORTAL_IDENTITY_REPO:
            return plugins

        kept = [p for p in plugins if _identity_host(p) != PORTAL_IDENTITY_HOST]
        dropped = len(plugins) - len(kept)
        if dropped:
            # Loud enough to debug a misconfigured repository -- by far the
            # likelier cause than a hostile one -- without narrating details
            # back to whoever served it.
            from hcli.lib.console import stderr_console

            stderr_console.print(
                f"[yellow]Warning:[/yellow] repository {name!r} served {dropped} plugin(s) claiming "
                f"Hex-Rays identities; ignored"
            )
        return kept

    def names(self) -> list[str]:
        return list(self.repositories)

    def failures(self) -> list[RepoFailure]:
        """Repositories that could not be consulted during this run."""
        return [self._failures[n] for n in self.repositories if n in self._failures]

    def get_plugins_in(self, name: str) -> list[Plugin]:
        """Plugins from one named repository. Propagates that repository's failure."""
        if name not in self.repositories:
            raise KeyError(f"unknown plugin repository: {name}")
        return list(self._load(name))

    def get_plugins_across(self, names: list[str]) -> list[Plugin]:
        """Plugins from several repositories, skipping those that cannot be fetched.

        Surveying is best-effort by design: one unreachable or unauthorized
        repository must not hide the results of the others. Callers report the
        skips via failures().
        """
        plugins: list[Plugin] = []
        for name in names:
            try:
                plugins.extend(self._load(name))
            except Exception as e:
                # Recorded by _load() and reported by failures(); the point of a
                # survey is that the reachable repositories still answer.
                logger.debug("skipping plugin repository %s: %s", name, e)
        return plugins

    def get_plugins(self) -> list[Plugin]:
        """Every plugin hcli can see, across every configured repository."""
        return self.get_plugins_across(self.names())

    def repo_of(self, plugin: Plugin) -> str | None:
        """Which loaded repository served this plugin."""
        for name, plugins in self._plugins.items():
            if any(p is plugin for p in plugins):
                return name
        return None
