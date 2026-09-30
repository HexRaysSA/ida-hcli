"""Tests for `ScopedPluginRepo`."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hcli.lib.ida import PluginRepository
from hcli.lib.ida.plugin import IDAMetadataDescriptor
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin, PluginArchiveLocation
from hcli.lib.ida.plugin.repo.aggregate import AggregatePluginRepo
from hcli.lib.ida.plugin.repo.scoped import ScopedPluginRepo

HOST = "https://github.com/test/test"


def _make_location(tmp_path: Path, name: str, version: str, *, sha256: str | None = None) -> PluginArchiveLocation:
    archive = tmp_path / f"{name}-{version}-{len(list(tmp_path.iterdir()))}.zip"
    archive.write_bytes(f"{name} {version}".encode())
    metadata = IDAMetadataDescriptor.model_validate(
        {
            "IDAMetadataDescriptorVersion": 1,
            "plugin": {
                "name": name,
                "version": version,
                "entryPoint": f"{name}.py",
                "urls": {"repository": HOST},
                "authors": [{"name": "Test", "email": "test@example.com"}],
                "platforms": ["linux-x86_64"],
            },
        }
    )
    return PluginArchiveLocation(
        url=archive.as_uri(),
        sha256=sha256 or hashlib.sha256(archive.read_bytes()).hexdigest(),
        metadata=metadata,
    )


class RecordingPluginRepo(BasePluginRepo):
    """A repository built from locations, recording `get_plugins` calls and fetched URLs."""

    def __init__(self, *locations: PluginArchiveLocation) -> None:
        self.plugins: dict[str, Plugin] = {}
        for location in locations:
            meta = location.metadata.plugin
            plugin = self.plugins.setdefault(meta.name, Plugin(name=meta.name, host=meta.host, versions={}))
            plugin.versions.setdefault(meta.version, []).append(location)
        self.calls = 0
        self.fetched: list[str] = []

    def get_plugins(self) -> list[Plugin]:
        self.calls += 1
        return list(self.plugins.values())

    def _fetch_and_verify(self, location: PluginArchiveLocation) -> tuple[str, bytes]:
        self.fetched.append(location.url)
        return super()._fetch_and_verify(location)


def test_scoped_repo_reads_each_repository_once(tmp_path):
    inner = RecordingPluginRepo(_make_location(tmp_path, "a", "1.0.0"))
    named = RecordingPluginRepo(_make_location(tmp_path, "b", "1.0.0"))
    repo = ScopedPluginRepo(inner, {"b": named})

    repo.get_plugins()
    repo.get_plugins()

    assert (inner.calls, named.calls) == (1, 1)


def test_scoped_repo_takes_named_plugins_from_the_named_repository_only(tmp_path):
    inner = RecordingPluginRepo(_make_location(tmp_path, "a", "2.0.0"), _make_location(tmp_path, "b", "1.0.0"))
    named = RecordingPluginRepo(_make_location(tmp_path, "a", "1.0.0"), _make_location(tmp_path, "c", "1.0.0"))
    repo = ScopedPluginRepo(inner, {"A": named})

    plugins = {plugin.name: sorted(plugin.versions) for plugin in repo.get_plugins()}

    assert plugins == {"a": ["1.0.0"], "b": ["1.0.0"]}


def test_scoped_repo_fetches_each_archive_once_from_the_repository_that_listed_it(tmp_path):
    a = _make_location(tmp_path, "a", "1.0.0")
    b = _make_location(tmp_path, "b", "1.0.0")
    inner = RecordingPluginRepo(a)
    named = RecordingPluginRepo(b)
    repo = ScopedPluginRepo(inner, {"b": named})

    assert repo.fetch_plugin_location(a) == ("a", b"a 1.0.0")
    assert repo.fetch_plugin_location(b) == ("b", b"b 1.0.0")
    repo.fetch_plugin_location(a)

    assert (inner.fetched, named.fetched) == ([a.url], [b.url])


def test_scoped_repo_verifies_the_archive_hash(tmp_path):
    a = _make_location(tmp_path, "a", "1.0.0", sha256="0" * 64)
    repo = ScopedPluginRepo(RecordingPluginRepo(a))

    with pytest.raises(ValueError, match="hash mismatch"):
        repo.fetch_plugin_location(a)


def test_scoped_repo_merges_one_plugin_listed_by_two_configured_repositories(tmp_path):
    from test_plugin_bundle_commands import _make_plugin_zip

    for repo_name, versions in {"main": ["1.0.0"], "other": ["1.0.0", "2.0.0"]}.items():
        repo_dir = tmp_path / repo_name
        repo_dir.mkdir()
        for version in versions:
            (repo_dir / f"b-{version}.zip").write_bytes(_make_plugin_zip("b", version))
    aggregate = AggregatePluginRepo(
        {
            name: PluginRepository(name=name, url=(tmp_path / name).as_uri(), reserved=False)
            for name in ("main", "other")
        }
    )
    repo = ScopedPluginRepo(aggregate)

    plugins = repo.get_plugins()

    assert [(plugin.name, sorted(plugin.versions)) for plugin in plugins] == [("b", ["1.0.0", "2.0.0"])]
    fetched = {
        (version, repo.fetch_plugin_location(location)[1])
        for version, locations in plugins[0].versions.items()
        for location in locations
    }
    assert fetched == {
        ("1.0.0", (tmp_path / "main" / "b-1.0.0.zip").read_bytes()),
        ("1.0.0", (tmp_path / "other" / "b-1.0.0.zip").read_bytes()),
        ("2.0.0", (tmp_path / "other" / "b-2.0.0.zip").read_bytes()),
    }
