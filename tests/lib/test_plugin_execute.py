"""Tests for install preparation and the journaled executor."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fixtures import *
from test_plugin_bundle import _build_bundle_zip, _make_manifest
from test_plugin_resolve import HOST, _fs_repo, _manifest, _repo, _zip

from hcli.lib.ida import find_current_ida_platform, get_ida_config
from hcli.lib.ida.plugin import IDAMetadataDescriptor
from hcli.lib.ida.plugin.exceptions import (
    DependencyInstallationError,
    DependencyUnavailableError,
    InstallExecutionError,
    PlanMetadataMismatchError,
)
from hcli.lib.ida.plugin.execute import InstallResult, execute_install, prepare_install
from hcli.lib.ida.plugin.install import get_plugins_directory, get_trash_directory
from hcli.lib.ida.plugin.reference import parse_plugin_reference
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin, PluginArchiveIndex, PluginArchiveLocation
from hcli.lib.ida.plugin.repo.bundle import PluginBundleRepo
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo
from hcli.lib.ida.plugin.resolve import (
    ArchiveRoot,
    EditableRoot,
    InstallPlan,
    RepositoryRoot,
    ResolutionContext,
    plan_install,
)


def _context(repo: BasePluginRepo | None) -> ResolutionContext:
    return ResolutionContext.from_environment(repo)


def _plan(context: ResolutionContext, repo: BasePluginRepo, *names: str, upgrade: bool = False) -> InstallPlan:
    return plan_install(context, [RepositoryRoot(parse_plugin_reference(n), repo, upgrade=upgrade) for n in names])


def _run(context: ResolutionContext, plan: InstallPlan, **kwargs) -> InstallResult:
    with prepare_install(plan, context, check_environment=False) as prepared:
        return execute_install(prepared, **kwargs)


def _installed_names() -> set[str]:
    plugins = get_plugins_directory()
    if not plugins.exists():
        return set()
    return {p.name for p in plugins.iterdir() if p.name != get_trash_directory(plugins).name}


def _installed_version(name: str) -> str:
    return json.loads((get_plugins_directory() / name / "ida-plugin.json").read_text())["plugin"]["version"]


def _break_destination(name: str) -> None:
    """Simulate a concurrent writer leaving junk where a planned node will be published."""
    destination = get_plugins_directory() / name
    destination.mkdir(parents=True)
    (destination / "junk.txt").write_text("not a plugin")


def _committed(result: InstallResult) -> list[str]:
    return [n.name for n in result.committed_operations]


def test_chain_installs_every_node_in_order(virtual_ida_environment, tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=["b"]), _zip("b", deps=["c"]), _zip("c"))
    context = _context(repo)
    result = _run(context, _plan(context, repo, "a"))

    assert _committed(result) == ["c", "b", "a"]
    assert {n.outcome for n in result.nodes} == {"installed"}
    assert _installed_names() == {"a", "b", "c"}
    assert result.pip_attempted is False
    assert list(get_trash_directory(get_plugins_directory()).iterdir()) == []


def test_retained_dependency_is_reported_present_not_installed(virtual_ida_environment, tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("b"))
    context = _context(repo)
    _run(context, _plan(context, repo, "b"))

    repo = _fs_repo(tmp_path / "repo2", _zip("a", deps=["b"]), _zip("b", "2.0.0"))
    context = _context(repo)
    result = _run(context, _plan(context, repo, "a"))

    assert _committed(result) == ["a"]
    assert [n.name for n in result.present] == ["b"]
    assert _installed_version("b") == "1.0.0"


def test_unfetchable_required_artifact_fails_before_any_mutation(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["b"]), _zip("b"))
    context = _context(repo)
    plan = _plan(context, repo, "a")

    with pytest.raises(DependencyUnavailableError, match="failed to fetch"):
        prepare_install(plan, context, check_environment=False)

    assert _installed_names() == set()


def test_required_failure_rolls_back_everything_new(virtual_ida_environment, tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=["b"]), _zip("b", deps=["c"]), _zip("c"))
    context = _context(repo)
    plan = _plan(context, repo, "a")
    _break_destination("a")

    with pytest.raises(InstallExecutionError) as excinfo:
        _run(context, plan)

    result = excinfo.value.result
    assert isinstance(result, InstallResult)
    assert result.committed_operations == []
    assert [(n.name, n.outcome) for n in result.nodes] == [("c", "rolled_back"), ("b", "rolled_back")]
    assert result.recovery is None
    assert _installed_names() == {"a"}
    assert (get_plugins_directory() / "a" / "junk.txt").exists()
    assert list(get_trash_directory(get_plugins_directory()).iterdir()) == []


def test_optional_dependency_with_missing_child_is_not_installed(virtual_ida_environment, tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", deps=[{"plugin": "b", "required": False}]),
        _zip("b", deps=["c"]),
    )
    context = _context(repo)
    result = _run(context, _plan(context, repo, "a"))

    assert _committed(result) == ["a"]
    assert _installed_names() == {"a"}
    assert len(result.unavailable_optionals) == 1
    branch, reason = result.unavailable_optionals[0]
    assert branch.edge.spec.plugin == "b"
    assert "c" in reason
    assert result.node_for_name("b") is None


def test_optional_branch_failure_is_rolled_back_to_its_savepoint(virtual_ida_environment, tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", deps=[{"plugin": "b", "required": False}]),
        _zip("b", deps=["c"]),
        _zip("c"),
    )
    context = _context(repo)
    plan = _plan(context, repo, "a")
    _break_destination("b")

    result = _run(context, plan)

    assert _committed(result) == ["a"]
    assert _installed_names() == {"a", "b"}
    assert not (get_plugins_directory() / "c").exists()
    assert {n.name: n.outcome for n in result.nodes} == {"a": "installed", "b": "unavailable", "c": "unavailable"}
    assert len(result.unavailable_optionals) == 1


def test_optional_branch_artifact_failure_recorded_at_prepare(virtual_ida_environment, tmp_path):
    a_zip = _zip("a", deps=[{"plugin": "b", "required": False}])
    (tmp_path / "a.zip").write_bytes(a_zip)
    index = PluginArchiveIndex()
    index.index_plugin_archive(a_zip, (tmp_path / "a.zip").as_uri())
    index.index_plugin_archive(_zip("b"), "mem://b.zip")
    repo = JSONFilePluginRepo(index.get_plugins())
    context = _context(repo)
    plan = _plan(context, repo, "a")

    with prepare_install(plan, context, check_environment=False) as prepared:
        assert list(prepared.branch_failures) == [0]
        result = execute_install(prepared)

    assert _committed(result) == ["a"]
    assert _installed_names() == {"a"}
    assert "failed to fetch" in result.unavailable_optionals[0][1]


def test_failed_root_restores_upgraded_dependency_exactly(virtual_ida_environment, tmp_path):
    repo = _fs_repo(tmp_path / "old", _zip("b", "1.0.0"))
    context = _context(repo)
    _run(context, _plan(context, repo, "b"))
    extra = get_plugins_directory() / "b" / "notes.txt"
    extra.write_text("local edits")

    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=["b==2.0.0", "c"]), _zip("b", "2.0.0"), _zip("c"))
    context = _context(repo)
    plan = _plan(context, repo, "a")
    b = plan.node_for_name("b")
    assert b is not None and b.operation == "upgrade"
    _break_destination("a")

    with pytest.raises(InstallExecutionError):
        _run(context, plan)

    assert _installed_version("b") == "1.0.0"
    assert extra.read_text() == "local edits"
    assert not (get_plugins_directory() / "c").exists()
    assert list(get_trash_directory(get_plugins_directory()).iterdir()) == []


def _write_editable_source(directory: Path, name: str, version: str, *, deps: list | None = None) -> Path:
    directory.mkdir(parents=True)
    (directory / "ida-plugin.json").write_text(json.dumps(_manifest(name, version, deps=deps)))
    (directory / f"{name}.py").write_text("# plugin")
    return directory


def test_editable_replacement_failure_restores_previous_link(virtual_ida_environment, tmp_path):
    first = _write_editable_source(tmp_path / "first", "a", "1.0.0")
    second = _write_editable_source(tmp_path / "second", "a", "1.1.0", deps=["c"])
    repo = _fs_repo(tmp_path / "repo", _zip("c"))
    context = _context(None)
    _run(context, plan_install(context, [EditableRoot(first)]))
    assert (get_plugins_directory() / "a").resolve() == first.resolve()

    context = _context(repo)
    plan = plan_install(context, [EditableRoot(second)])
    _break_destination("c")

    with pytest.raises(InstallExecutionError):
        _run(context, plan)

    link = get_plugins_directory() / "a"
    assert link.is_symlink() and link.resolve() == first.resolve()


def test_hash_mismatch_fails_before_mutation(virtual_ida_environment, tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=["b"]), _zip("b"))
    context = _context(repo)
    plan = _plan(context, repo, "a")
    for archive in (tmp_path / "repo").glob("archive-*.zip"):
        archive.write_bytes(archive.read_bytes() + b"tampered")

    with pytest.raises(DependencyUnavailableError, match=r"hash mismatch|does not match"):
        prepare_install(plan, context, check_environment=False)

    assert _installed_names() == set()


def _repo_with_tampered_index(tmp_path: Path) -> JSONFilePluginRepo:
    """An index whose entry for ``b`` claims a dependency the archive does not declare."""
    zip_data = _zip("b")
    path = tmp_path / "b.zip"
    path.write_bytes(zip_data)
    metadata = IDAMetadataDescriptor.model_validate(_manifest("b", deps=["c"]))
    location = PluginArchiveLocation(url=path.as_uri(), sha256=hashlib.sha256(zip_data).hexdigest(), metadata=metadata)
    a_zip = _zip("a", deps=["b"])
    index = PluginArchiveIndex()
    index.index_plugin_archive(a_zip, (tmp_path / "a.zip").as_uri())
    (tmp_path / "a.zip").write_bytes(a_zip)
    c_zip = _zip("c")
    index.index_plugin_archive(c_zip, (tmp_path / "c.zip").as_uri())
    (tmp_path / "c.zip").write_bytes(c_zip)
    plugins = [p for p in index.get_plugins() if p.name != "b"]
    plugins.append(Plugin(name="b", host=HOST, versions={"1.0.0": [location]}))
    return JSONFilePluginRepo(plugins)


def test_index_metadata_mismatch_fails_before_mutation(virtual_ida_environment, tmp_path):
    repo = _repo_with_tampered_index(tmp_path)
    context = _context(repo)
    plan = _plan(context, repo, "a")
    assert [i.name for i in plan.order] == ["c", "b", "a"]

    with pytest.raises(PlanMetadataMismatchError, match="dependencies"):
        prepare_install(plan, context, check_environment=False)

    assert _installed_names() == set()


def _settings() -> list[dict]:
    return [{"key": "token", "type": "string", "name": "Token", "documentation": "d", "required": True}]


def test_configuration_is_written_and_rolled_back_with_the_plan(virtual_ida_environment, tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=["b"]), _zip("b", settings=_settings()))
    context = _context(repo)
    plan = _plan(context, repo, "a")
    _break_destination("a")

    with pytest.raises(InstallExecutionError):
        _run(context, plan, config_values={("b", "token"): "secret"})
    assert "b" not in get_ida_config().plugins

    shutil.rmtree(get_plugins_directory() / "a")
    context = _context(repo)
    result = _run(context, _plan(context, repo, "a"), config_values={("b", "token"): "secret"})
    assert _committed(result) == ["b", "a"]
    assert get_ida_config().plugins["b"].settings == {"token": "secret"}


def test_local_archive_root_with_repository_dependency(virtual_ida_environment, tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("b"))
    context = _context(repo)
    plan = plan_install(context, [ArchiveRoot(_zip("a", deps=["b"]))])
    result = _run(context, plan)

    assert _committed(result) == ["b", "a"]
    assert _installed_names() == {"a", "b"}


def test_bundle_backed_root_and_dependency_install_offline(virtual_ida_environment, block_network, tmp_path):
    manifest = _make_manifest(
        targetPlatformTags=[
            {
                "id": "local",
                "idaPlatform": find_current_ida_platform(),
                "pythonVersion": "3.12",
                "implementation": "cp",
                "abis": ["cp312", "abi3", "none"],
                "pipPlatformTags": ["any"],
                "wheelhouse": "dependencies/python/local",
            }
        ]
    )
    bundle = _build_bundle_zip(manifest, plugin_zips={"a.zip": _zip("a", deps=["b"]), "b.zip": _zip("b")})
    path = tmp_path / "bundle.zip"
    path.write_bytes(bundle)
    repo = PluginBundleRepo(path)
    context = _context(repo)
    plan = _plan(context, repo, "a")

    with prepare_install(plan, context, check_environment=False) as prepared:
        assert prepared.pip_options.find_links == ()
        result = execute_install(prepared)

    assert _committed(result) == ["b", "a"]
    assert _installed_names() == {"a", "b"}


def _pip_freeze() -> str:
    python_exe = Path(os.environ["HCLI_CURRENT_IDA_PYTHON_EXE"])
    return subprocess.run([str(python_exe), "-m", "pip", "freeze"], capture_output=True, text=True, check=True).stdout


def test_conflicting_python_requirements_fail_before_mutation(virtual_ida_environment_with_venv, tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", deps=["b"], python_deps=["packaging==25.0"]),
        _zip("b", python_deps=["packaging==24.0"]),
    )
    context = _context(repo)
    plan = _plan(context, repo, "a")

    with pytest.raises(DependencyInstallationError):
        prepare_install(plan, context)

    assert _installed_names() == set()
    assert "packaging" not in _pip_freeze()


def test_optional_branch_with_conflicting_python_requirement_is_skipped(virtual_ida_environment_with_venv, tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", deps=[{"plugin": "b", "required": False}], python_deps=["packaging==25.0"]),
        _zip("b", python_deps=["packaging==24.0"]),
    )
    context = _context(repo)
    plan = _plan(context, repo, "a")

    with prepare_install(plan, context) as prepared:
        result = execute_install(prepared)

    assert _committed(result) == ["a"]
    assert result.pip_attempted is True
    assert _installed_names() == {"a"}
    assert len(result.unavailable_optionals) == 1
    assert "packaging==25.0" in _pip_freeze()
