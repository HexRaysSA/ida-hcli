"""Tests for bundle creation with the required dependency closure."""

from __future__ import annotations

import sys

import pytest
from click.testing import CliRunner
from fixtures import *
from test_plugin_resolve import _fs_repo, _zip

from hcli.commands.plugin.bundle import bundle
from hcli.lib.ida import find_current_ida_platform
from hcli.lib.ida.plugin.bundle import plan_bundle_contents
from hcli.lib.ida.plugin.exceptions import DependencyUnavailableError
from hcli.lib.ida.plugin.execute import execute_install, prepare_install
from hcli.lib.ida.plugin.install import get_plugins_directory, get_trash_directory, install_plugin_archive
from hcli.lib.ida.plugin.reference import parse_plugin_reference
from hcli.lib.ida.plugin.repo.bundle import PluginBundleRepo
from hcli.lib.ida.plugin.resolve import RepositoryRoot, ResolutionContext, plan_install
from hcli.lib.ida.python import PipOptions

PLATFORMS = ["linux-x86_64", "windows-x86_64"]


def _names(contents) -> dict[str, str]:
    return {a.name: a.version for a in contents.archives}


def test_plan_bundle_contents_includes_required_closure(tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=["b"]), _zip("b", deps=["c"]), _zip("c"))

    contents = plan_bundle_contents(["a"], repo, PLATFORMS)

    assert _names(contents) == {"a": "1.0.0", "b": "1.0.0", "c": "1.0.0"}
    assert {a.name for a in contents.archives if a.is_root} == {"a"}
    assert all(a.platforms == tuple(PLATFORMS) for a in contents.archives)


def test_plan_bundle_contents_excludes_optional_only_dependencies(tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=[{"plugin": "b", "required": False}]), _zip("b"))

    contents = plan_bundle_contents(["a"], repo, PLATFORMS)
    assert _names(contents) == {"a": "1.0.0"}

    contents = plan_bundle_contents(["a", "b"], repo, PLATFORMS)
    assert _names(contents) == {"a": "1.0.0", "b": "1.0.0"}


def test_plan_bundle_contents_ignores_plugins_installed_on_builder(virtual_ida_environment, tmp_path):
    install_plugin_archive(_zip("b"), "b", check_environment=False)
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=["b"]))

    with pytest.raises(DependencyUnavailableError, match="b"):
        plan_bundle_contents(["a"], repo, PLATFORMS)


def test_plan_bundle_contents_unpinned_spec_selects_latest(tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("a", "1.0.0"), _zip("a", "2.0.0"))

    assert _names(plan_bundle_contents(["a"], repo, PLATFORMS)) == {"a": "2.0.0"}
    assert _names(plan_bundle_contents(["a==1.0.0"], repo, PLATFORMS)) == {"a": "1.0.0"}


def test_plan_bundle_contents_dedups_shared_dependency(tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=["b"]), _zip("c", deps=["b"]), _zip("b"))

    contents = plan_bundle_contents(["a", "c"], repo, PLATFORMS)

    assert sorted(a.name for a in contents.archives) == ["a", "b", "c"]
    assert len(contents.archives_for_name("b")) == 1


def test_plan_bundle_contents_local_root_with_repository_dependency(tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("b", python_deps=["packaging"]))
    local = tmp_path / "a.zip"
    local.write_bytes(_zip("a", deps=["b"], python_deps=["rich"]))

    contents = plan_bundle_contents([str(local)], repo, PLATFORMS)

    assert _names(contents) == {"a": "1.0.0", "b": "1.0.0"}
    assert sorted(contents.python_requirements) == ["packaging", "rich"]


def test_plan_bundle_contents_platform_specific_archives(tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", platforms=["linux-x86_64"]),
        _zip("a", platforms=["windows-x86_64"]),
    )

    contents = plan_bundle_contents(["a"], repo, PLATFORMS)

    archives = contents.archives_for_name("a")
    assert len(archives) == 2
    assert sorted(a.platforms for a in archives) == [("linux-x86_64",), ("windows-x86_64",)]


def _current_target_id() -> str:
    return f"{find_current_ida_platform()}-cp{sys.version_info.major}{sys.version_info.minor}"


def _installed_names() -> set[str]:
    plugins = get_plugins_directory()
    if not plugins.exists():
        return set()
    return {p.name for p in plugins.iterdir() if p.name != get_trash_directory(plugins).name}


def test_bundle_create_includes_dependencies_and_installs_offline(virtual_ida_environment, block_network, tmp_path):
    repo_dir = tmp_path / "repo"
    _fs_repo(repo_dir, _zip("a", deps=["b"]), _zip("b"))
    out = tmp_path / "bundle.zip"

    result = CliRunner().invoke(
        bundle,
        ["create", "--path", str(out), "--target", _current_target_id(), "--repo", str(repo_dir), "a"],
        obj={"pip_options": PipOptions()},
    )
    assert result.exit_code == 0, result.output
    assert "plugins: 2" in result.output

    bundle_repo = PluginBundleRepo(out)
    try:
        assert sorted(p.name for p in bundle_repo.get_plugins()) == ["a", "b"]
        context = ResolutionContext.from_environment(bundle_repo)
        plan = plan_install(context, [RepositoryRoot(parse_plugin_reference("a"), bundle_repo)])
        with prepare_install(plan, context, check_environment=False) as prepared:
            execute_install(prepared)
    finally:
        bundle_repo.close()

    assert _installed_names() == {"a", "b"}


def test_bundle_create_fails_when_required_dependency_missing(tmp_path):
    repo_dir = tmp_path / "repo"
    _fs_repo(repo_dir, _zip("a", deps=["missing-dep"]))
    out = tmp_path / "bundle.zip"

    result = CliRunner().invoke(
        bundle,
        ["create", "--path", str(out), "--target", "linux-x86_64-cp312", "--repo", str(repo_dir), "a"],
        obj={"pip_options": PipOptions()},
    )

    assert result.exit_code != 0
    assert "missing-dep" in result.output
    assert not out.exists()


def test_bundle_create_accepts_unpinned_repository_spec(tmp_path):
    repo_dir = tmp_path / "repo"
    _fs_repo(repo_dir, _zip("a", "1.0.0"), _zip("a", "1.5.0"))
    out = tmp_path / "bundle.zip"

    result = CliRunner().invoke(
        bundle,
        ["create", "--path", str(out), "--target", "linux-x86_64-cp312", "--repo", str(repo_dir), "a"],
        obj={"pip_options": PipOptions()},
    )

    assert result.exit_code == 0, result.output
    bundle_repo = PluginBundleRepo(out)
    try:
        (plugin,) = bundle_repo.get_plugins()
        assert list(plugin.versions) == ["1.5.0"]
    finally:
        bundle_repo.close()


def test_plan_bundle_contents_reports_omitted_optional_dependencies(tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", deps=[{"plugin": "b", "required": False}, {"plugin": "ghost", "required": False}]),
        _zip("b"),
    )

    contents = plan_bundle_contents(["a"], repo, PLATFORMS)

    omitted = {(o.spec, o.declared_by): o.reason for o in contents.omitted_optional}
    assert set(omitted) == {("b", "a"), ("ghost", "a")}
    assert omitted[("b", "a")] == "optional dependencies are bundled only when named as roots"
    assert "ghost" in omitted[("ghost", "a")]

    contents = plan_bundle_contents(["a", "b"], repo, PLATFORMS)
    assert [(o.spec, o.declared_by) for o in contents.omitted_optional] == [("ghost", "a")]


def test_bundle_create_prints_omitted_optional_dependencies(tmp_path):
    repo_dir = tmp_path / "repo"
    _fs_repo(repo_dir, _zip("a", deps=[{"plugin": "b", "required": False}]), _zip("b"))
    out = tmp_path / "bundle.zip"

    result = CliRunner().invoke(
        bundle,
        ["create", "--path", str(out), "--target", "linux-x86_64-cp312", "--repo", str(repo_dir), "a"],
        obj={"pip_options": PipOptions()},
    )

    assert result.exit_code == 0, result.output
    assert "omitted optional dependency: b (used by a)" in result.output
