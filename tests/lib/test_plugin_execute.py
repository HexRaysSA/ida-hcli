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
from fixtures import temp_env_var
from test_plugin_bundle import _build_bundle_zip, _make_manifest
from test_plugin_resolve import HOST, _fs_repo, _manifest, _repo, _suite_zip, _zip

from hcli.lib.ida import find_current_ida_platform, get_ida_config
from hcli.lib.ida.plugin import IDAMetadataDescriptor
from hcli.lib.ida.plugin.exceptions import (
    BrokenPluginInstallationError,
    BundleTargetUnavailableError,
    DependencyInstallationError,
    DependencyUnavailableError,
    InstallExecutionError,
    PlanMetadataMismatchError,
)
from hcli.lib.ida.plugin.execute import InstallResult, execute_install, prepare_install
from hcli.lib.ida.plugin.install import (
    get_plugins_directory,
    get_trash_directory,
    install_plugin_archive,
    uninstall_plugin,
)
from hcli.lib.ida.plugin.reference import parse_plugin_reference
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin, PluginArchiveIndex, PluginArchiveLocation
from hcli.lib.ida.plugin.repo.bundle import PluginBundleRepo
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo
from hcli.lib.ida.plugin.resolve import (
    ArchiveRoot,
    EditableRoot,
    InstalledRoot,
    InstallPlan,
    RepositoryRoot,
    ResolutionContext,
    plan_install,
)
from hcli.lib.ida.plugin.transaction import InstallTransaction, PreconditionChangedError


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


def _rolled_back(error: InstallExecutionError) -> list[str]:
    assert isinstance(error.result, InstallResult)
    return [n.display for n in error.result.nodes if n.outcome == "rolled_back"]


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
    txn = _FailingTransaction(get_plugins_directory(), "a", PermissionError("disk is read-only"))

    with (
        pytest.raises(InstallExecutionError) as excinfo,
        prepare_install(plan, context, check_environment=False) as prepared,
    ):
        execute_install(prepared, transaction=txn)

    result = excinfo.value.result
    assert isinstance(result, InstallResult)
    assert result.committed_operations == []
    assert [(n.name, n.outcome) for n in result.nodes] == [("c", "rolled_back"), ("b", "rolled_back")]
    assert result.recovery is None
    assert _installed_names() == set()
    txn.rollback()
    assert list(get_trash_directory(get_plugins_directory()).iterdir()) == []


def test_broken_destination_of_last_node_fails_before_any_mutation(virtual_ida_environment, tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=["b"]), _zip("b", deps=["c"]), _zip("c"))
    context = _context(repo)
    plan = _plan(context, repo, "a")
    _break_destination("a")

    with pytest.raises(BrokenPluginInstallationError):
        _run(context, plan)

    assert _installed_names() == {"a"}
    assert (get_plugins_directory() / "a" / "junk.txt").exists()
    assert not get_trash_directory(get_plugins_directory()).exists()


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
    assert [n.name for n in result.nodes] == ["a"]


def test_optional_branch_broken_destination_is_skipped_before_it_starts(virtual_ida_environment, tmp_path):
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
    assert "remnants" in result.unavailable_optionals[0][1]
    assert "rolled back" not in result.unavailable_optionals[0][1]


def test_optional_branch_rollback_names_the_nodes_it_undid(virtual_ida_environment, tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", deps=[{"plugin": "b", "required": False}]),
        _zip("b", deps=["c"]),
        _zip("c"),
    )
    context = _context(repo)
    plan = _plan(context, repo, "a")
    txn = _FailingTransaction(get_plugins_directory(), "b", PreconditionChangedError("b changed underneath us"))

    with prepare_install(plan, context, check_environment=False) as prepared:
        result = execute_install(prepared, transaction=txn)

    assert _committed(result) == ["a"]
    assert _installed_names() == {"a"}
    assert {n.name: n.outcome for n in result.nodes} == {"a": "installed", "b": "unavailable", "c": "unavailable"}
    assert result.unavailable_optionals[0][1] == "b changed underneath us; rolled back c==1.0.0"
    txn.commit()
    assert list(get_trash_directory(get_plugins_directory()).iterdir()) == []


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
    txn = _FailingTransaction(get_plugins_directory(), "a", PermissionError("disk is read-only"))

    with (
        pytest.raises(InstallExecutionError) as excinfo,
        prepare_install(plan, context, check_environment=False) as prepared,
    ):
        execute_install(prepared, transaction=txn)

    assert _rolled_back(excinfo.value) == ["b==2.0.0", "c==1.0.0"]
    assert _installed_version("b") == "1.0.0"
    assert extra.read_text() == "local edits"
    assert not (get_plugins_directory() / "c").exists()
    txn.rollback()
    assert list(get_trash_directory(get_plugins_directory()).iterdir()) == []


def _write_editable_source(directory: Path, name: str, version: str, *, deps: list | None = None) -> Path:
    directory.mkdir(parents=True)
    (directory / "ida-plugin.json").write_text(json.dumps(_manifest(name, version, deps=deps)))
    (directory / f"{name}.py").write_text("# plugin")
    return directory


def test_editable_replacement_failure_restores_previous_link(virtual_ida_environment, tmp_path):
    first = _write_editable_source(tmp_path / "first", "a", "1.0.0")
    second = _write_editable_source(tmp_path / "second", "a", "1.1.0")
    context = _context(None)
    _run(context, plan_install(context, [EditableRoot(first)]))
    assert (get_plugins_directory() / "a").resolve() == first.resolve()

    plan = plan_install(context, [EditableRoot(second), ArchiveRoot(_zip("b"))])
    assert [n.name for n in plan.ordered_nodes()] == ["a", "b"]
    txn = _FailingTransaction(get_plugins_directory(), "b", PermissionError("disk is read-only"))

    with (
        pytest.raises(InstallExecutionError) as excinfo,
        prepare_install(plan, context, check_environment=False) as prepared,
    ):
        execute_install(prepared, transaction=txn)

    assert _rolled_back(excinfo.value) == ["a==1.1.0"]
    link = get_plugins_directory() / "a"
    assert link.is_symlink() and link.resolve() == first.resolve()
    txn.rollback()
    assert list(get_trash_directory(get_plugins_directory()).iterdir()) == []


def _install_b_then_remove_c(tmp_path: Path) -> BasePluginRepo:
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=["b"]), _zip("b", deps=["c"]), _zip("c"))
    install_plugin_archive(_zip("b", deps=["c"]), "b", plugin_repo=repo, check_environment=False)
    uninstall_plugin("c")
    assert _installed_names() == {"b"}
    return repo


def test_install_repairs_installed_dependency_missing_its_child(virtual_ida_environment, tmp_path):
    repo = _install_b_then_remove_c(tmp_path)

    result = install_plugin_archive(_zip("a", deps=["b"]), "a", plugin_repo=repo, check_environment=False)

    assert _committed(result) == ["c", "a"]
    assert [r.name for r in result.present] == ["b"]
    assert _installed_names() == {"a", "b", "c"}


def test_installed_root_reinstalls_missing_dependency(virtual_ida_environment, tmp_path):
    repo = _install_b_then_remove_c(tmp_path)
    context = _context(repo)

    result = _run(context, plan_install(context, [InstalledRoot("b")]))

    assert _committed(result) == ["c"]
    assert [r.name for r in result.present] == ["b"]
    assert _installed_names() == {"b", "c"}


def test_installed_root_without_repo_fails_when_child_missing(virtual_ida_environment, tmp_path):
    _install_b_then_remove_c(tmp_path)
    context = _context(None)

    with pytest.raises(DependencyUnavailableError, match="no plugin repository"):
        plan_install(context, [InstalledRoot("b")])

    assert _installed_names() == {"b"}


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


class _FailingAfterConfigTransaction(InstallTransaction):
    """Writes the setting, then fails, so the written value must be rolled back."""

    def set_config_key(self, plugin_name: str, key: str, value, metadata, **kwargs) -> None:
        super().set_config_key(plugin_name, key, value, metadata, **kwargs)
        raise PermissionError("config store is read-only")


def test_configuration_is_written_and_rolled_back_with_the_plan(virtual_ida_environment, tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=["b"]), _zip("b", settings=_settings()))
    context = _context(repo)
    plan = _plan(context, repo, "a")
    txn = _FailingAfterConfigTransaction(get_plugins_directory())

    with (
        pytest.raises(InstallExecutionError, match="config store is read-only") as excinfo,
        prepare_install(plan, context, check_environment=False) as prepared,
    ):
        execute_install(prepared, transaction=txn, config_values={("b", "token"): "secret"})
    assert _rolled_back(excinfo.value) == ["b==1.0.0", "a==1.0.0"]
    assert "b" not in get_ida_config().plugins
    assert _installed_names() == set()
    txn.rollback()

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


def test_later_optional_branch_with_different_selection_is_skipped_as_conflict(virtual_ida_environment, tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", deps=[{"plugin": "x", "required": False}, {"plugin": "y", "required": False}]),
        _zip("x", deps=["d==1.0.0"]),
        _zip("y", deps=["d==2.0.0"]),
        _zip("d", "1.0.0"),
        _zip("d", "2.0.0"),
    )
    context = _context(repo)
    result = _run(context, _plan(context, repo, "a"))

    assert _installed_names() == {"a", "x", "d"}
    assert _installed_version("d") == "1.0.0"
    assert [
        (b.edge.spec.plugin, "reselection is unsupported" in reason) for b, reason in result.unavailable_optionals
    ] == [("y", True)]
    assert [d.kind for d in result.execution_diagnostics] == ["conflict"]
    assert {n.name: n.outcome for n in result.nodes if n.branch == 1} == {"y": "unavailable"}


def test_later_optional_branch_with_same_selection_reuses_accepted_node(virtual_ida_environment, tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", deps=[{"plugin": "x", "required": False}, {"plugin": "y", "required": False}]),
        _zip("x", deps=["d"]),
        _zip("y", deps=["d==1.0.0"]),
        _zip("d", "1.0.0"),
    )
    context = _context(repo)
    result = _run(context, _plan(context, repo, "a"))

    assert _installed_names() == {"a", "x", "y", "d"}
    assert result.unavailable_optionals == []
    assert result.execution_diagnostics == []


class _FailingTransaction(InstallTransaction):
    """Injects a failure into the publication of one named plugin directory."""

    def __init__(self, plugins_dir: Path, target: str, error: BaseException):
        super().__init__(plugins_dir)
        self.target = target
        self.error = error

    def publish_directory(self, staged: Path, destination: Path) -> None:
        if destination.name == self.target:
            raise self.error
        super().publish_directory(staged, destination)


def test_filesystem_error_in_optional_branch_aborts_whole_operation(virtual_ida_environment, tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=[{"plugin": "b", "required": False}]), _zip("b"))
    context = _context(repo)
    plan = _plan(context, repo, "a")
    txn = _FailingTransaction(get_plugins_directory(), "b", PermissionError("disk is read-only"))

    with (
        pytest.raises(InstallExecutionError, match="disk is read-only") as excinfo,
        prepare_install(plan, context, check_environment=False) as prepared,
    ):
        execute_install(prepared, transaction=txn)

    assert isinstance(excinfo.value.__cause__, PermissionError)
    assert [(n.name, n.outcome) for n in excinfo.value.result.nodes] == [("a", "rolled_back")]
    assert excinfo.value.result.unavailable_optionals == []
    assert _installed_names() == set()
    assert txn.journal == []
    txn.rollback()


def test_keyboard_interrupt_rolls_back_and_carries_result(virtual_ida_environment, tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=["b"]), _zip("b"))
    context = _context(repo)
    plan = _plan(context, repo, "a")
    txn = _FailingTransaction(get_plugins_directory(), "a", KeyboardInterrupt())

    with (
        pytest.raises(KeyboardInterrupt) as excinfo,
        prepare_install(plan, context, check_environment=False) as prepared,
    ):
        execute_install(prepared, transaction=txn)

    result = excinfo.value.install_result
    assert [(n.name, n.outcome) for n in result.nodes] == [("b", "rolled_back")]
    assert _installed_names() == set()
    txn.rollback()


def test_unexpected_error_before_mutation_propagates_unchanged(virtual_ida_environment, tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("a"))
    context = _context(repo)
    plan = _plan(context, repo, "a")
    txn = _FailingTransaction(get_plugins_directory(), "a", RuntimeError("boom"))

    with (
        pytest.raises(RuntimeError, match="boom"),
        prepare_install(plan, context, check_environment=False) as prepared,
    ):
        execute_install(prepared, transaction=txn)

    assert _installed_names() == set()
    txn.rollback()


def test_repair_of_retained_root_uses_installed_tree_and_never_fetches_root(virtual_ida_environment, tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("my-pack", deps=["b"]), _zip("b"))
    context = _context(repo)
    _run(context, _plan(context, repo, "my-pack"))
    shutil.rmtree(get_plugins_directory() / "b")

    (tmp_path / "b.zip").write_bytes(_zip("b"))
    index = PluginArchiveIndex()
    index.index_plugin_archive(_zip("my-pack", deps=["c"]), "mem://my-pack.zip")
    index.index_plugin_archive(_zip("b"), (tmp_path / "b.zip").as_uri())
    index.index_plugin_archive(_zip("c"), "mem://c.zip")
    stale = JSONFilePluginRepo(index.get_plugins())
    context = _context(stale)
    plan = _plan(context, stale, "my-pack", upgrade=True)

    root = plan.nodes[plan.roots[0]]
    assert root.operation == "retain"
    assert root.source.__class__.__name__ == "InstalledSource"
    assert [identity.name for identity in plan.order] == ["b", "my-pack"]

    result = _run(context, plan)

    assert _committed(result) == ["b"]
    assert [n.name for n in result.present] == ["my-pack"]
    assert _installed_names() == {"my-pack", "b"}


def test_bundle_without_matching_target_fails_before_mutation_when_requirements_exist(
    virtual_ida_environment, tmp_path
):
    current = find_current_ida_platform()
    other = "windows-x86_64" if current != "windows-x86_64" else "linux-x86_64"
    manifest = _make_manifest(
        targetPlatformTags=[
            {
                "id": "elsewhere",
                "idaPlatform": other,
                "pythonVersion": "3.12",
                "implementation": "cp",
                "abis": ["cp312", "abi3", "none"],
                "pipPlatformTags": ["any"],
                "wheelhouse": "dependencies/python/elsewhere",
            }
        ]
    )
    bundle = _build_bundle_zip(manifest, plugin_zips={"a.zip": _zip("a", python_deps=["packaging==25.0"])})
    path = tmp_path / "bundle.zip"
    path.write_bytes(bundle)
    repo = PluginBundleRepo(path)
    context = _context(repo)
    plan = _plan(context, repo, "a")

    with pytest.raises(BundleTargetUnavailableError, match="elsewhere"):
        prepare_install(plan, context, check_environment=False)

    assert _installed_names() == set()


def _mixed_repo(tmp_path: Path, reachable: list[bytes], unreachable: list[bytes]) -> JSONFilePluginRepo:
    """An index where only ``reachable`` archives can be fetched."""
    index = PluginArchiveIndex()
    for i, buf in enumerate(reachable):
        path = tmp_path / f"reachable-{i}.zip"
        path.write_bytes(buf)
        index.index_plugin_archive(buf, path.as_uri())
    for i, buf in enumerate(unreachable):
        index.index_plugin_archive(buf, f"mem://unreachable-{i}.zip")
    return JSONFilePluginRepo(index.get_plugins())


def test_branch_artifacts_are_keyed_by_selection_not_identity(virtual_ida_environment, tmp_path):
    repo = _mixed_repo(
        tmp_path,
        reachable=[
            _zip("a", deps=[{"plugin": "x", "required": False}, {"plugin": "y", "required": False}]),
            _zip("y", deps=["d==2.0.0"]),
            _zip("d", "1.0.0"),
            _zip("d", "2.0.0"),
        ],
        unreachable=[_zip("x", deps=["d==1.0.0"])],
    )
    context = _context(repo)
    plan = _plan(context, repo, "a")

    with prepare_install(plan, context, check_environment=False) as prepared:
        assert list(prepared.branch_failures) == [0]
        assert sorted((key[0].name, key[1]) for key in prepared.artifacts) == [
            ("a", "1.0.0"),
            ("d", "1.0.0"),
            ("d", "2.0.0"),
            ("y", "1.0.0"),
        ]
        result = execute_install(prepared)

    assert _installed_names() == {"a", "y", "d"}
    assert _installed_version("d") == "2.0.0"
    assert [b.edge.spec.plugin for b, _ in result.unavailable_optionals] == ["x"]


def test_rolled_back_branch_does_not_leave_stale_acceptance_for_later_branches(virtual_ida_environment, tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", deps=[{"plugin": "x", "required": False}, {"plugin": "y", "required": False}]),
        _zip("x", deps=["d==1.0.0"]),
        _zip("y", deps=["d==2.0.0"]),
        _zip("d", "1.0.0"),
        _zip("d", "2.0.0"),
    )
    context = _context(repo)
    plan = _plan(context, repo, "a")
    _break_destination("x")

    result = _run(context, plan)

    assert _installed_names() == {"a", "x", "y", "d"}
    assert _installed_version("d") == "2.0.0"
    assert [b.edge.spec.plugin for b, _ in result.unavailable_optionals] == ["x"]
    assert result.execution_diagnostics == []
    assert {n.name: n.outcome for n in result.nodes if n.branch == 1} == {"d": "installed", "y": "installed"}


def test_later_branch_claiming_a_name_owned_by_an_earlier_branch_is_skipped(virtual_ida_environment, tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", deps=[{"plugin": "foo", "required": False}, {"plugin": "suite", "required": False}]),
        _zip("foo"),
        _suite_zip("suite", "1.0.0", [("foo", "2.0.0", {})]),
    )
    context = _context(repo)
    result = _run(context, _plan(context, repo, "a"))

    assert _installed_names() == {"a", "foo"}
    assert [
        (b.edge.spec.plugin, "'foo'" in reason and "foo already owns" in reason)
        for b, reason in result.unavailable_optionals
    ] == [("suite", True)]
    assert [(d.kind, d.target and d.target.name) for d in result.execution_diagnostics] == [("conflict", "suite")]


def test_later_branch_whose_name_is_a_component_of_an_earlier_branch_is_skipped(virtual_ida_environment, tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", deps=[{"plugin": "suite", "required": False}, {"plugin": "foo", "required": False}]),
        _zip("foo"),
        _suite_zip("suite", "1.0.0", [("foo", "2.0.0", {})]),
    )
    context = _context(repo)
    result = _run(context, _plan(context, repo, "a"))

    assert _installed_names() == {"a", "suite"}
    assert [(b.edge.spec.plugin, "suite already owns" in reason) for b, reason in result.unavailable_optionals] == [
        ("foo", True)
    ]
    assert [d.kind for d in result.execution_diagnostics] == ["conflict"]


def test_bundle_without_matching_target_proceeds_when_only_retained_nodes_need_python(
    virtual_ida_environment_with_venv, tmp_path
):
    fs = _fs_repo(tmp_path / "repo", _zip("lib", python_deps=["packaging==25.0"]))
    context = _context(fs)
    _run(context, _plan(context, fs, "lib"))

    current = find_current_ida_platform()
    other = "windows-x86_64" if current != "windows-x86_64" else "linux-x86_64"
    manifest = _make_manifest(
        targetPlatformTags=[
            {
                "id": "elsewhere",
                "idaPlatform": other,
                "pythonVersion": "3.12",
                "implementation": "cp",
                "abis": ["cp312", "abi3", "none"],
                "pipPlatformTags": ["any"],
                "wheelhouse": "dependencies/python/elsewhere",
            }
        ]
    )
    bundle = _build_bundle_zip(manifest, plugin_zips={"a.zip": _zip("a", deps=["lib"])})
    path = tmp_path / "bundle.zip"
    path.write_bytes(bundle)
    repo = PluginBundleRepo(path)
    context = _context(repo)
    plan = _plan(context, repo, "a")

    result = _run(context, plan)

    assert _committed(result) == ["a"]
    assert [n.name for n in result.present] == ["lib"]


def test_installed_plugin_requirements_join_preflight(virtual_ida_environment_with_venv, tmp_path):
    install_plugin_archive(_zip("c", python_deps=["packaging==24.0"]), "c", check_environment=False)
    repo = _fs_repo(tmp_path / "repo", _zip("a", python_deps=["packaging==25.0"]))
    context = _context(repo)
    plan = _plan(context, repo, "a")

    with pytest.raises(DependencyInstallationError):
        prepare_install(plan, context)

    assert _installed_names() == {"c"}
    assert "packaging==24.0" in _pip_freeze()


def test_broken_destination_is_detected_before_pip(virtual_ida_environment_with_venv, tmp_path):
    repo = _fs_repo(tmp_path / "repo", _zip("a", python_deps=["packaging==25.0"]))
    context = _context(repo)
    plan = _plan(context, repo, "a")
    _break_destination("a")

    with pytest.raises(BrokenPluginInstallationError):
        _run(context, plan)

    assert _installed_names() == {"a"}
    assert "packaging" not in _pip_freeze()


def test_optional_branch_broken_destination_skips_pip(virtual_ida_environment_with_venv, tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", deps=[{"plugin": "b", "required": False}]),
        _zip("b", python_deps=["packaging==25.0"]),
    )
    context = _context(repo)
    plan = _plan(context, repo, "a")
    _break_destination("b")

    with prepare_install(plan, context) as prepared:
        result = execute_install(prepared)

    assert _committed(result) == ["a"]
    assert result.pip_attempted is False
    assert len(result.unavailable_optionals) == 1
    assert "packaging" not in _pip_freeze()


def test_bundle_without_matching_target_only_skips_optional_branches_with_requirements(
    virtual_ida_environment, tmp_path
):
    current = find_current_ida_platform()
    other = "windows-x86_64" if current != "windows-x86_64" else "linux-x86_64"
    manifest = _make_manifest(
        targetPlatformTags=[
            {
                "id": "elsewhere",
                "idaPlatform": other,
                "pythonVersion": "3.12",
                "implementation": "cp",
                "abis": ["cp312", "abi3", "none"],
                "pipPlatformTags": ["any"],
                "wheelhouse": "dependencies/python/elsewhere",
            }
        ]
    )
    bundle = _build_bundle_zip(
        manifest,
        plugin_zips={
            "a.zip": _zip("a", deps=[{"plugin": "b", "required": False}, {"plugin": "c", "required": False}]),
            "b.zip": _zip("b", python_deps=["packaging==25.0"]),
            "c.zip": _zip("c"),
        },
    )
    path = tmp_path / "bundle.zip"
    path.write_bytes(bundle)
    repo = PluginBundleRepo(path)
    context = _context(repo)
    plan = _plan(context, repo, "a")

    result = _run(context, plan)

    assert _committed(result) == ["a", "c"]
    assert result.pip_attempted is False
    assert {n.name: n.outcome for n in result.nodes} == {"a": "installed", "b": "unavailable", "c": "installed"}
    assert len(result.unavailable_optionals) == 1
    assert "elsewhere" in result.unavailable_optionals[0][1]


def test_shared_optional_dependency_with_setting_survives_both_branches(virtual_ida_environment, tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", deps=[{"plugin": "b", "required": False}, {"plugin": "c", "required": False}]),
        _zip("b", deps=["d"]),
        _zip("c", deps=["d"]),
        _zip("d", settings=_settings()),
    )
    context = _context(repo)
    plan = _plan(context, repo, "a")

    result = _run(context, plan, config_values={("d", "token"): "shared"})

    assert result.unavailable_optionals == []
    assert set(_committed(result)) == {"a", "b", "c", "d"}
    assert _installed_names() == {"a", "b", "c", "d"}
    assert get_ida_config().plugins["d"].settings == {"token": "shared"}


def test_optional_branch_without_python_is_skipped_and_root_kept(virtual_ida_environment, tmp_path):
    repo = _fs_repo(
        tmp_path / "repo",
        _zip("a", deps=[{"plugin": "b", "required": False}]),
        _zip("b", python_deps=["packaging==25.0"]),
    )
    context = _context(repo)
    plan = _plan(context, repo, "a")

    with temp_env_var("IDAPYTHON_VENV_EXECUTABLE", str(tmp_path / "missing-python")):
        result = _run(context, plan)

    assert _committed(result) == ["a"]
    assert _installed_names() == {"a"}
    assert result.pip_attempted is False
    assert [b.edge.spec.plugin for b, _ in result.unavailable_optionals] == ["b"]
    assert "python" in result.unavailable_optionals[0][1].lower()


def test_configuration_for_retained_dependency_is_written(virtual_ida_environment, tmp_path):
    install_plugin_archive(
        _zip("b", settings=_settings()), "b", check_environment=False, config_values={("b", "token"): "old"}
    )
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=["b"]))
    context = _context(repo)
    plan = _plan(context, repo, "a")
    assert plan.observed_settings == {("b", "token"): (True, "old")}

    result = _run(context, plan, config_values={("b", "token"): "new"})

    assert _committed(result) == ["a"]
    assert [n.name for n in result.present] == ["b"]
    assert get_ida_config().plugins["b"].settings == {"token": "new"}


def test_configuration_precondition_detects_setting_changed_after_planning(virtual_ida_environment, tmp_path):
    from hcli.lib.ida.plugin.settings import set_plugin_setting

    install_plugin_archive(
        _zip("b", settings=_settings()), "b", check_environment=False, config_values={("b", "token"): "old"}
    )
    repo = _fs_repo(tmp_path / "repo", _zip("a", deps=["b"]))
    context = _context(repo)
    plan = _plan(context, repo, "a")
    set_plugin_setting("b", "token", "changed-elsewhere")

    with pytest.raises(InstallExecutionError) as excinfo:
        _run(context, plan, config_values={("b", "token"): "new"})

    assert isinstance(excinfo.value.__cause__, PreconditionChangedError)
    assert _rolled_back(excinfo.value) == ["a==1.0.0"]
    assert _installed_names() == {"b"}
    assert get_ida_config().plugins["b"].settings == {"token": "changed-elsewhere"}


def test_bundle_without_matching_target_proceeds_when_requirements_come_from_another_repo(
    virtual_ida_environment_with_venv, tmp_path
):
    from hcli.lib.ida import PluginRepository
    from hcli.lib.ida.plugin.repo.aggregate import AggregatePluginRepo

    fs_dir = tmp_path / "fs"
    _fs_repo(fs_dir, _zip("lib", python_deps=["packaging==25.0"]))
    current = find_current_ida_platform()
    other = "windows-x86_64" if current != "windows-x86_64" else "linux-x86_64"
    manifest = _make_manifest(
        targetPlatformTags=[
            {
                "id": "elsewhere",
                "idaPlatform": other,
                "pythonVersion": "3.12",
                "implementation": "cp",
                "abis": ["cp312", "abi3", "none"],
                "pipPlatformTags": ["any"],
                "wheelhouse": "dependencies/python/elsewhere",
            }
        ]
    )
    bundle_path = tmp_path / "bundle.zip"
    bundle_path.write_bytes(_build_bundle_zip(manifest, plugin_zips={"a.zip": _zip("a", deps=["lib"])}))
    repo = AggregatePluginRepo(
        {
            "bundle": PluginRepository(name="bundle", url=bundle_path.as_uri(), reserved=False),
            "fs": PluginRepository(name="fs", url=fs_dir.as_uri(), reserved=False),
        }
    )
    context = _context(repo)
    plan = _plan(context, repo, "a")

    result = _run(context, plan)

    assert _committed(result) == ["lib", "a"]
    assert result.pip_attempted is True
    assert "packaging==25.0" in _pip_freeze()
