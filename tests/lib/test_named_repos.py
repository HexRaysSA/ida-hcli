"""Tests for named plugin repository support: type dispatch, aggregate loading, and config."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from hcli.lib.ida import (
    COMMUNITY_REPO_NAME,
    HEXRAYS_REPO_NAME,
    RESERVED_PLUGIN_REPOSITORIES,
    IDAConfigJson,
    PluginRepository,
    PluginRepositoryConfig,
    get_plugin_repositories,
)
from hcli.lib.ida.plugin.repo import repo_from_url
from hcli.lib.ida.plugin.repo.aggregate import AggregatePluginRepo
from hcli.lib.ida.plugin.repo.bundle import PluginBundleRepo
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo
from hcli.lib.ida.plugin.repo.fs import FileSystemPluginRepo

TESTS_DIR = Path(__file__).parent.parent
PLUGIN1_V1 = TESTS_DIR / "data" / "plugins" / "plugin1" / "plugin1-v1.0.0.zip"
PLUGIN1_V2 = TESTS_DIR / "data" / "plugins" / "plugin1" / "plugin1-v2.0.0.zip"


def _make_json_repo_file(tmp_path: Path, *plugin_zips: Path) -> Path:
    from hcli.lib.ida.plugin.repo import PluginArchiveIndex

    index = PluginArchiveIndex()
    for zip_path in plugin_zips:
        data = zip_path.read_bytes()
        index.index_plugin_archive(data, zip_path.as_uri())
    plugins = index.get_plugins()
    json_repo = JSONFilePluginRepo(plugins)
    p = tmp_path / "plugin-repository.json"
    p.write_text(json_repo.to_json(), encoding="utf-8")
    return p


def _make_bundle_manifest() -> dict:
    return {
        "version": 1,
        "kind": "hcli-plugin-bundle",
        "builtAt": "2026-04-28T16:00:00Z",
        "createdBy": {"tool": "hcli", "version": "0.0.0"},
        "targetPlatformTags": [
            {
                "id": "linux-x86_64-cp312",
                "idaPlatform": "linux-x86_64",
                "pythonVersion": "3.12",
                "implementation": "cp",
                "abis": ["cp312", "abi3", "none"],
                "pipPlatformTags": ["manylinux_2_28_x86_64"],
                "wheelhouse": "dependencies/python/linux-x86_64-cp312",
            }
        ],
    }


def _make_bundle_zip(plugin_zips: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    manifest = _make_bundle_manifest()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin-bundle.json", json.dumps(manifest))
        if plugin_zips:
            for name, data in plugin_zips.items():
                zf.writestr(f"plugins/{name}", data)
        for target in manifest["targetPlatformTags"]:
            wh = target["wheelhouse"]
            zf.writestr(f"{wh}/placeholder.whl", b"fake-wheel")
    return buf.getvalue()


def _write_bundle(tmp_path: Path) -> Path:
    p = tmp_path / "bundle.zip"
    p.write_bytes(_make_bundle_zip({"plugin1-v1.0.0.zip": PLUGIN1_V1.read_bytes()}))
    return p


def _make_fs_repo(tmp_path: Path) -> Path:
    d = tmp_path / "fs-repo"
    d.mkdir()
    import shutil

    shutil.copy(PLUGIN1_V1, d / "plugin1-v1.0.0.zip")
    return d


# --- repo_from_url dispatch ---


def test_repo_from_url_json_file(tmp_path):
    json_path = _make_json_repo_file(tmp_path, PLUGIN1_V1)
    repo = repo_from_url(json_path.as_uri())
    assert isinstance(repo, JSONFilePluginRepo)
    assert len(repo.get_plugins()) >= 1


def test_repo_from_url_bundle(tmp_path):
    bundle_path = _write_bundle(tmp_path)
    repo = repo_from_url(bundle_path.as_uri())
    assert isinstance(repo, PluginBundleRepo)
    plugins = repo.get_plugins()
    assert len(plugins) >= 1
    repo.close()


def test_repo_from_url_directory(tmp_path):
    fs_path = _make_fs_repo(tmp_path)
    repo = repo_from_url(fs_path.as_uri())
    assert isinstance(repo, FileSystemPluginRepo)
    assert len(repo.get_plugins()) >= 1


def test_repo_from_url_https_treated_as_json():
    import httpx

    with pytest.raises((httpx.ConnectError, httpx.TimeoutException)):
        repo_from_url("https://nonexistent.invalid/plugin-repository.json")


# --- AggregatePluginRepo with different child types ---


def test_aggregate_loads_json_repo(tmp_path):
    json_path = _make_json_repo_file(tmp_path, PLUGIN1_V1)
    repos = {"test": PluginRepository(name="test", url=json_path.as_uri(), reserved=False)}
    agg = AggregatePluginRepo(repos)
    plugins = agg.get_plugins()
    assert len(plugins) >= 1
    child = agg.get_child_repo("test")
    assert isinstance(child, JSONFilePluginRepo)


def test_aggregate_loads_bundle_repo(tmp_path):
    bundle_path = _write_bundle(tmp_path)
    repos = {"offline": PluginRepository(name="offline", url=bundle_path.as_uri(), reserved=False)}
    agg = AggregatePluginRepo(repos)
    plugins = agg.get_plugins()
    assert len(plugins) >= 1
    child = agg.get_child_repo("offline")
    assert isinstance(child, PluginBundleRepo)


def test_aggregate_loads_fs_repo(tmp_path):
    fs_path = _make_fs_repo(tmp_path)
    repos = {"local": PluginRepository(name="local", url=fs_path.as_uri(), reserved=False)}
    agg = AggregatePluginRepo(repos)
    plugins = agg.get_plugins()
    assert len(plugins) >= 1
    child = agg.get_child_repo("local")
    assert isinstance(child, FileSystemPluginRepo)


def test_aggregate_child_repo_preserves_bundle_type(tmp_path):
    bundle_path = _write_bundle(tmp_path)
    repos = {"offline": PluginRepository(name="offline", url=bundle_path.as_uri(), reserved=False)}
    agg = AggregatePluginRepo(repos)
    agg.get_plugins()

    child = agg.get_child_repo("offline")
    assert isinstance(child, PluginBundleRepo)
    assert child.target_ids == ["linux-x86_64-cp312"]


def test_aggregate_mixed_repos(tmp_path):
    json_path = _make_json_repo_file(tmp_path, PLUGIN1_V1)
    bundle_path = _write_bundle(tmp_path)
    repos = {
        "json-repo": PluginRepository(name="json-repo", url=json_path.as_uri(), reserved=False),
        "bundle-repo": PluginRepository(name="bundle-repo", url=bundle_path.as_uri(), reserved=False),
    }
    agg = AggregatePluginRepo(repos)
    plugins = agg.get_plugins()
    assert len(plugins) >= 1

    assert isinstance(agg.get_child_repo("json-repo"), JSONFilePluginRepo)
    assert isinstance(agg.get_child_repo("bundle-repo"), PluginBundleRepo)


# --- Reserved repo removal ---


def _config_with_repos(**repos: str) -> IDAConfigJson:
    plugin_repos = {name: PluginRepositoryConfig(url=url) for name, url in repos.items()}
    config = IDAConfigJson()
    config.settings.plugin_repositories = plugin_repos
    return config


def test_reserved_repos_present_by_default():
    config = _config_with_repos(
        hexrays=RESERVED_PLUGIN_REPOSITORIES[HEXRAYS_REPO_NAME],
        community=RESERVED_PLUGIN_REPOSITORIES[COMMUNITY_REPO_NAME],
    )
    repos = get_plugin_repositories(config)
    assert HEXRAYS_REPO_NAME in repos
    assert COMMUNITY_REPO_NAME in repos
    assert repos[HEXRAYS_REPO_NAME].reserved
    assert repos[COMMUNITY_REPO_NAME].reserved


def test_reserved_repo_removed_when_absent_from_nonempty_config():
    config = _config_with_repos(
        community=RESERVED_PLUGIN_REPOSITORIES[COMMUNITY_REPO_NAME],
        custom="file:///tmp/my-repo/plugin-repository.json",
    )
    repos = get_plugin_repositories(config)
    assert HEXRAYS_REPO_NAME not in repos
    assert COMMUNITY_REPO_NAME in repos
    assert "custom" in repos


def test_both_reserved_repos_removed():
    config = _config_with_repos(
        custom="file:///tmp/my-repo/plugin-repository.json",
    )
    repos = get_plugin_repositories(config)
    assert HEXRAYS_REPO_NAME not in repos
    assert COMMUNITY_REPO_NAME not in repos
    assert "custom" in repos


def test_reserved_repos_injected_on_empty_config():
    config = IDAConfigJson()
    repos = get_plugin_repositories(config)
    assert HEXRAYS_REPO_NAME in repos
    assert COMMUNITY_REPO_NAME in repos
