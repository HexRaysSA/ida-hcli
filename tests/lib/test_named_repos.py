"""Tests for named plugin repository support: type dispatch, aggregate loading, and config."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import *

from hcli.commands.plugin import plugin as plugin_group
from hcli.lib.ida import (
    COMMUNITY_REPO_NAME,
    HEXRAYS_REPO_NAME,
    RESERVED_PLUGIN_REPOSITORIES,
    IDAConfigJson,
    PluginRepository,
    PluginRepositoryConfig,
    get_plugin_repositories,
)
from hcli.lib.ida.plugin.install import get_installed_plugin_records
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


def _current_platform_tag() -> dict:
    """Build a bundle target tag matching the current test environment."""
    import platform as _platform
    import sys

    system = _platform.system()
    if system == "Darwin":
        version = _platform.uname().version
        ida_platform = "macos-aarch64" if "RELEASE_ARM64" in version else "macos-x86_64"
    elif system == "Windows":
        ida_platform = "windows-x86_64"
    else:
        ida_platform = "linux-x86_64"

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    cp_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    tag_id = f"{ida_platform}-{cp_tag}"
    return {
        "id": tag_id,
        "idaPlatform": ida_platform,
        "pythonVersion": py_ver,
        "implementation": "cp",
        "abis": [cp_tag, "abi3", "none"],
        "pipPlatformTags": ["any"],
        "wheelhouse": f"dependencies/python/{tag_id}",
    }


def _make_bundle_manifest() -> dict:
    return {
        "version": 1,
        "kind": "hcli-plugin-bundle",
        "builtAt": "2026-04-28T16:00:00Z",
        "createdBy": {"tool": "hcli", "version": "0.0.0"},
        "targetPlatformTags": [_current_platform_tag()],
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
    assert child.target_ids == [_current_platform_tag()["id"]]


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


# --- CLI integration: airgapped workflow with named bundle repo ---


def _write_bundle_with_plugins(tmp_path: Path, *plugin_zips: Path) -> Path:
    buf = io.BytesIO()
    manifest = _make_bundle_manifest()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin-bundle.json", json.dumps(manifest))
        for zip_path in plugin_zips:
            zf.writestr(f"plugins/{zip_path.name}", zip_path.read_bytes())
        for target in manifest["targetPlatformTags"]:
            wh = target["wheelhouse"]
            zf.writestr(f"{wh}/placeholder.whl", b"fake-wheel")
    p = tmp_path / "plugins-offline.zip"
    p.write_bytes(buf.getvalue())
    return p


def _write_fs_repo_with_plugins(tmp_path: Path, *plugin_zips: Path) -> Path:
    d = tmp_path / "fs-repo"
    d.mkdir()
    for zip_path in plugin_zips:
        shutil.copy(zip_path, d / zip_path.name)
    return d


def _invoke(runner: CliRunner, *args: str):
    return runner.invoke(plugin_group, list(args))


def test_airgapped_workflow_bundle_repo(virtual_ida_environment_with_venv, block_network, tmp_path):
    """Full airgapped workflow: remove default repos, add a bundle, search, install."""
    bundle_path = _write_bundle_with_plugins(tmp_path, PLUGIN1_V1, PLUGIN1_V2)
    bundle_url = bundle_path.as_uri()
    runner = CliRunner(mix_stderr=False)

    result = _invoke(runner, "repo", "remove", "hexrays")
    assert result.exit_code == 0, result.output
    assert "removed" in result.output

    result = _invoke(runner, "repo", "remove", "community")
    assert result.exit_code == 0, result.output
    assert "removed" in result.output

    result = _invoke(runner, "repo", "add", "offline", bundle_url)
    assert result.exit_code == 0, result.output
    assert "added" in result.output

    result = _invoke(runner, "repo", "set-default", "offline")
    assert result.exit_code == 0, result.output

    result = _invoke(runner, "repo", "list")
    assert result.exit_code == 0, result.output
    assert "offline" in result.output
    assert "hexrays" not in result.output
    assert "community" not in result.output

    result = _invoke(runner, "search")
    assert result.exit_code == 0, result.output
    assert "plugin1" in result.output

    result = _invoke(runner, "install", "offline/plugin1==1.0.0")
    assert result.exit_code == 0, result.output
    assert "plugin1" in result.output
    installed = [(r.name, r.version) for r in get_installed_plugin_records()]
    assert ("plugin1", "1.0.0") in installed

    result = _invoke(runner, "upgrade", "offline/plugin1==2.0.0")
    assert result.exit_code == 0, result.output
    installed = [(r.name, r.version) for r in get_installed_plugin_records()]
    assert ("plugin1", "2.0.0") in installed


def test_airgapped_workflow_fs_repo(virtual_ida_environment, block_network, tmp_path):
    """Full airgapped workflow with a directory repo."""
    fs_path = _write_fs_repo_with_plugins(tmp_path, PLUGIN1_V1, PLUGIN1_V2)
    fs_url = fs_path.as_uri()
    runner = CliRunner(mix_stderr=False)

    result = _invoke(runner, "repo", "remove", "hexrays")
    assert result.exit_code == 0, result.output

    result = _invoke(runner, "repo", "remove", "community")
    assert result.exit_code == 0, result.output

    result = _invoke(runner, "repo", "add", "local", fs_url)
    assert result.exit_code == 0, result.output

    result = _invoke(runner, "repo", "set-default", "local")
    assert result.exit_code == 0, result.output

    result = _invoke(runner, "search")
    assert result.exit_code == 0, result.output
    assert "plugin1" in result.output

    result = _invoke(runner, "install", "local/plugin1==1.0.0")
    assert result.exit_code == 0, result.output
    installed = [(r.name, r.version) for r in get_installed_plugin_records()]
    assert ("plugin1", "1.0.0") in installed


def test_restore_reserved_repo_after_removal(virtual_ida_environment):
    """A removed reserved repo can be restored with repo add and its canonical URL."""
    runner = CliRunner(mix_stderr=False)

    result = _invoke(runner, "repo", "remove", "hexrays")
    assert result.exit_code == 0, result.output

    result = _invoke(runner, "repo", "list")
    assert result.exit_code == 0, result.output
    assert "hexrays" not in result.output

    canonical = RESERVED_PLUGIN_REPOSITORIES[HEXRAYS_REPO_NAME]
    result = _invoke(runner, "repo", "add", "hexrays", canonical)
    assert result.exit_code == 0, result.output

    result = _invoke(runner, "repo", "list")
    assert result.exit_code == 0, result.output
    assert "hexrays" in result.output


def test_reserved_repo_cannot_be_repointed(virtual_ida_environment):
    """Adding a reserved repo with a non-canonical URL is rejected."""
    runner = CliRunner(mix_stderr=False)

    result = _invoke(runner, "repo", "add", "hexrays", "https://evil.example.com/repo.json")
    assert result.exit_code != 0
    assert "reserved" in result.output
