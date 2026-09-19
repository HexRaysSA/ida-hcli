"""Tests for the pure install planner."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fixtures import *

from hcli.lib.ida import IDAConfigJson, PluginConfig, PluginRepository, find_current_ida_platform
from hcli.lib.ida.plugin import ALL_PLATFORMS, IDAMetadataDescriptor
from hcli.lib.ida.plugin.exceptions import (
    BrokenPluginInstallationError,
    DependencyConflictError,
    DependencyResolutionError,
    DependencyTargetsComponentError,
    DependencyUnavailableError,
    IncompleteMetadataError,
    InstalledPluginNameConflictError,
    PlatformIncompatibleError,
    PluginAlreadyInstalledError,
    PluginNotFoundError,
    PluginVersionDowngradeError,
)
from hcli.lib.ida.plugin.install import (
    extract_zip_subdirectory_to,
    get_plugins_directory,
    install_plugin_archive,
    uninstall_plugin,
)
from hcli.lib.ida.plugin.reference import parse_plugin_reference
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin, PluginArchiveIndex, PluginArchiveLocation
from hcli.lib.ida.plugin.repo.aggregate import AggregatePluginRepo
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo
from hcli.lib.ida.plugin.repo.fs import FileSystemPluginRepo
from hcli.lib.ida.plugin.resolve import (
    ArchiveRoot,
    EditableRoot,
    EditableSource,
    InstalledSource,
    InstallPlan,
    LocalArchiveSource,
    LocationRoot,
    LocationSource,
    PluginIdentity,
    RepositoryRoot,
    ResolutionContext,
    plan_install,
)

HOST = "https://github.com/test/pack"
OTHER_HOST = "https://github.com/other/pack"


def _manifest(
    name: str,
    version: str = "1.0.0",
    *,
    deps: list | None = None,
    components: list[str] | None = None,
    python_deps: list[str] | str | None = None,
    settings: list[dict] | None = None,
    platforms: list[str] | None = None,
    host: str = HOST,
) -> dict:
    plugin: dict = {
        "name": name,
        "version": version,
        "entryPoint": f"{name}.py",
        "urls": {"repository": host},
        "authors": [{"name": "Test", "email": "test@example.com"}],
    }
    if deps is not None:
        plugin["dependencies"] = deps
    if components is not None:
        plugin["components"] = components
    if python_deps is not None:
        plugin["pythonDependencies"] = python_deps
    if settings is not None:
        plugin["settings"] = settings
    if platforms is not None:
        plugin["platforms"] = platforms
    return {"IDAMetadataDescriptorVersion": 1, "plugin": plugin}


def _zip(name: str, version: str = "1.0.0", **kwargs) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/ida-plugin.json", json.dumps(_manifest(name, version, **kwargs)))
        zf.writestr(f"{name}/{name}.py", "# plugin")
    return buf.getvalue()


def _suite_zip(name: str, version: str, components: list[tuple[str, str, dict]], **kwargs) -> bytes:
    buf = io.BytesIO()
    manifest = _manifest(name, version, components=[c[0] for c in components], **kwargs)
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/ida-plugin.json", json.dumps(manifest))
        zf.writestr(f"{name}/{name}.py", "# suite")
        for comp_name, comp_version, comp_kwargs in components:
            zf.writestr(
                f"{name}/{comp_name}/ida-plugin.json", json.dumps(_manifest(comp_name, comp_version, **comp_kwargs))
            )
            zf.writestr(f"{name}/{comp_name}/{comp_name}.py", "# component")
    return buf.getvalue()


def _place_installed(buf: bytes, name: str) -> Path:
    """Unpack an archive into the plugins directory without running the installer, so pip never runs."""
    destination = get_plugins_directory() / name
    extract_zip_subdirectory_to(buf, Path(name), destination)
    return destination


def _repo(*archives: bytes) -> JSONFilePluginRepo:
    """An index whose archive URLs cannot be fetched, so planning must not download."""
    index = PluginArchiveIndex()
    for i, buf in enumerate(archives):
        index.index_plugin_archive(buf, f"mem://archive-{i}.zip")
    return JSONFilePluginRepo(index.get_plugins())


def _fs_repo(directory: Path, *archives: bytes) -> FileSystemPluginRepo:
    directory.mkdir(parents=True, exist_ok=True)
    for i, buf in enumerate(archives):
        (directory / f"archive-{i}.zip").write_bytes(buf)
    return FileSystemPluginRepo(directory)


def _context(repo: BasePluginRepo | None, **kwargs) -> ResolutionContext:
    return ResolutionContext.from_environment(repo, **kwargs)


def _root(name: str, repo: BasePluginRepo, **kwargs) -> RepositoryRoot:
    return RepositoryRoot(parse_plugin_reference(name), repo, **kwargs)


def _plan(repo: BasePluginRepo | None, *roots: str, context: ResolutionContext | None = None, **kwargs) -> InstallPlan:
    assert repo is not None
    ctx = context or _context(repo)
    return plan_install(ctx, [_root(r, repo, **kwargs) for r in roots])


def _names(plan: InstallPlan) -> list[str]:
    return [identity.name for identity in plan.order]


def _node(plan: InstallPlan, name: str):
    node = plan.node_for_name(name)
    assert node is not None, f"{name} not planned"
    return node


def _other_platform() -> str:
    current = find_current_ida_platform()
    return next(p for p in sorted(ALL_PLATFORMS) if p != current)


# ---------------------------------------------------------------------------
# required traversal and first-visit selection
# ---------------------------------------------------------------------------


def test_chain_installs_in_dependency_order(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["b"]), _zip("b", deps=["c"]), _zip("c"))
    plan = _plan(repo, "a")
    assert _names(plan) == ["c", "b", "a"]
    assert plan.roots == [PluginIdentity("a", HOST)]
    assert {node.operation for node in plan.nodes.values()} == {"install"}
    assert _node(plan, "a").is_root and not _node(plan, "c").is_root
    assert _node(plan, "c").chain == ("a", "b", "c")
    assert all(isinstance(node.source, LocationSource) for node in plan.nodes.values())


def test_absent_unpinned_selects_latest_compatible(virtual_ida_environment):
    repo = _repo(
        _zip("a", deps=["b"]), _zip("b", "1.0.0"), _zip("b", "2.0.0"), _zip("b", "3.0.0", platforms=[_other_platform()])
    )
    plan = _plan(repo, "a")
    assert _node(plan, "b").version == "2.0.0"


def test_absent_pinned_selects_exact_version(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["b==1.0.0"]), _zip("b", "1.0.0"), _zip("b", "2.0.0"))
    plan = _plan(repo, "a")
    assert _node(plan, "b").version == "1.0.0"


def test_present_unpinned_retains_installed(virtual_ida_environment):
    install_plugin_archive(_zip("b", "1.0.0"), "b")
    repo = _repo(_zip("a", deps=["b"]), _zip("b", "2.0.0"))
    plan = _plan(repo, "a")
    b = _node(plan, "b")
    assert b.operation == "retain"
    assert b.version == "1.0.0"
    assert isinstance(b.source, InstalledSource)
    assert [n.name for n in plan.ordered_nodes() if n.mutates] == ["a"]


def test_present_at_pin_retains_installed(virtual_ida_environment):
    install_plugin_archive(_zip("b", "1.0.0"), "b")
    repo = _repo(_zip("a", deps=["b==1.0.0"]), _zip("b", "2.0.0"))
    plan = _plan(repo, "a")
    assert _node(plan, "b").operation == "retain"
    assert plan.warnings == []


def test_present_newer_than_pin_retains_with_warning(virtual_ida_environment):
    install_plugin_archive(_zip("b", "2.0.0"), "b")
    repo = _repo(_zip("a", deps=["b==1.0.0"]), _zip("b", "1.0.0"))
    plan = _plan(repo, "a")
    assert _node(plan, "b").operation == "retain"
    assert _node(plan, "b").version == "2.0.0"
    assert len(plan.warnings) == 1 and "newer than pin 1.0.0" in plan.warnings[0]


def test_present_older_than_pin_upgrades_to_exact_pin(virtual_ida_environment):
    install_plugin_archive(_zip("b", "1.0.0"), "b")
    repo = _repo(_zip("a", deps=["b==2.0.0"]), _zip("b", "2.0.0"), _zip("b", "3.0.0"))
    plan = _plan(repo, "a")
    b = _node(plan, "b")
    assert b.operation == "upgrade"
    assert b.version == "2.0.0"
    assert b.installed is not None and b.installed.version == "1.0.0"
    assert isinstance(b.source, LocationSource)


def test_installed_unqualified_dependency_anchors_to_installed_host(virtual_ida_environment):
    install_plugin_archive(_zip("b", "1.0.0"), "b")
    repo = _repo(_zip("a", deps=["b==2.0.0"]), _zip("b", "2.0.0"), _zip("b", "3.0.0", host=OTHER_HOST))
    plan = _plan(repo, "a")
    assert _node(plan, "b").identity == PluginIdentity("b", HOST)
    assert _node(plan, "b").version == "2.0.0"


def test_installed_dependency_from_other_host_conflicts(virtual_ida_environment):
    install_plugin_archive(_zip("b", "1.0.0"), "b")
    repo = _repo(_zip("a", deps=[f"b@{OTHER_HOST}"]), _zip("b", "1.0.0", host=OTHER_HOST))
    with pytest.raises(DependencyConflictError, match="different host"):
        _plan(repo, "a")


def test_names_are_case_insensitive(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["Dep-B", "dep-b"]), _zip("dep-b"))
    plan = _plan(repo, "a")
    assert _names(plan) == ["dep-b", "a"]


def test_missing_required_dependency_reports_chain(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["b"]), _zip("b", deps=["missing"]))
    with pytest.raises(DependencyUnavailableError) as e:
        _plan(repo, "a")
    assert e.value.chain == ("a", "b")
    assert "missing" in str(e.value) and "a -> b" in str(e.value)


# ---------------------------------------------------------------------------
# repeated visits
# ---------------------------------------------------------------------------


def test_diamond_visits_shared_dependency_once(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["b", "c"]), _zip("b", deps=["d"]), _zip("c", deps=["d"]), _zip("d"))
    plan = _plan(repo, "a")
    assert _names(plan) == ["d", "b", "c", "a"]
    kinds = [(d.edge.spec.plugin, d.kind) for d in plan.diagnostics if d.edge.spec.plugin == "d"]
    assert kinds == [("d", "selected"), ("d", "satisfied")]


def test_repeat_visit_lower_pin_keeps_newer_with_warning(virtual_ida_environment):
    repo = _repo(
        _zip("a", deps=["d==2.0.0", "c"]), _zip("c", deps=["d==1.0.0"]), _zip("d", "2.0.0"), _zip("d", "1.0.0")
    )
    plan = _plan(repo, "a")
    assert _node(plan, "d").version == "2.0.0"
    assert any(d.kind == "retained-newer" for d in plan.diagnostics)
    assert len(plan.warnings) == 1


def test_repeat_visit_higher_pin_conflicts_with_both_chains(virtual_ida_environment):
    repo = _repo(
        _zip("a", deps=["d==1.0.0", "c"]), _zip("c", deps=["d==2.0.0"]), _zip("d", "2.0.0"), _zip("d", "1.0.0")
    )
    with pytest.raises(DependencyConflictError) as e:
        _plan(repo, "a")
    message = str(e.value)
    assert "version reselection is unsupported" in message
    assert "declared by a)" in message
    assert "declared by a -> c" in message


def test_repeat_visit_other_host_conflicts(virtual_ida_environment):
    repo = _repo(
        _zip("a", deps=[f"d@{HOST}", "c"]), _zip("c", deps=[f"d@{OTHER_HOST}"]), _zip("d"), _zip("d", host=OTHER_HOST)
    )
    with pytest.raises(DependencyConflictError, match="also required from"):
        _plan(repo, "a")


def test_self_dependency_is_satisfied(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["a"]))
    plan = _plan(repo, "a")
    assert _names(plan) == ["a"]


def test_compatible_cycle_resolves(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["b"]), _zip("b", deps=["a==1.0.0"]))
    plan = _plan(repo, "a")
    assert _names(plan) == ["b", "a"]
    assert any("cycle" in d.message for d in plan.diagnostics)


def test_conflicting_cycle_fails(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["b"]), _zip("b", deps=["a==9.0.0"]))
    with pytest.raises(DependencyConflictError):
        _plan(repo, "a")


def test_long_chain_does_not_recurse(virtual_ida_environment):
    length = 120
    archives = [_zip(f"p{i}", deps=[f"p{i + 1}"]) for i in range(length)] + [_zip(f"p{length}")]
    plan = _plan(_repo(*archives), "p0")
    assert len(plan.order) == length + 1
    assert plan.order[0].name == f"p{length}"


def test_node_limit_is_enforced(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["b"]), _zip("b", deps=["c"]), _zip("c", deps=["d"]), _zip("d"))
    with pytest.raises(DependencyResolutionError, match="exceeds 2"):
        _plan(repo, "a", context=_context(repo, max_nodes=2))


# ---------------------------------------------------------------------------
# installed dependencies and no-repo planning
# ---------------------------------------------------------------------------


def _install_then_remove_child(tmp_path: Path, parent: bytes, parent_name: str, child_name: str) -> None:
    seed = _fs_repo(tmp_path / "seed", _zip(child_name))
    install_plugin_archive(parent, parent_name, plugin_repo=seed, check_environment=False)
    uninstall_plugin(child_name)


def test_installed_dependency_with_missing_required_child_is_repaired(virtual_ida_environment, tmp_path):
    _install_then_remove_child(tmp_path, _zip("b", deps=["c"]), "b", "c")
    repo = _repo(_zip("a", deps=["b"]), _zip("c"))
    plan = _plan(repo, "a")
    assert _names(plan) == ["c", "b", "a"]
    assert _node(plan, "b").operation == "retain"
    assert _node(plan, "c").operation == "install"


def test_installed_dependency_with_missing_child_and_no_repo_fails(virtual_ida_environment, tmp_path):
    _install_then_remove_child(tmp_path, _zip("b", deps=["c"]), "b", "c")
    with pytest.raises(DependencyUnavailableError, match="no plugin repository"):
        plan_install(_context(None), [ArchiveRoot(_zip("a", deps=["b"]))])


def test_no_repo_with_satisfied_dependencies_plans(virtual_ida_environment):
    install_plugin_archive(_zip("b"), "b")
    plan = plan_install(_context(None), [ArchiveRoot(_zip("a", deps=["b"]))])
    assert _names(plan) == ["b", "a"]


def test_no_repo_with_missing_optional_marks_branch_unavailable(virtual_ida_environment):
    plan = plan_install(_context(None), [ArchiveRoot(_zip("a", deps=[{"plugin": "opt", "required": False}]))])
    assert _names(plan) == ["a"]
    (branch,) = plan.optional_branches
    assert not branch.available
    assert "no plugin repository" in (branch.unavailable_reason or "")


# ---------------------------------------------------------------------------
# optional branches
# ---------------------------------------------------------------------------


def test_optional_branch_plans_its_required_closure_separately(virtual_ida_environment):
    repo = _repo(
        _zip("a", deps=["b", {"plugin": "opt", "required": False}]), _zip("b"), _zip("opt", deps=["x"]), _zip("x")
    )
    plan = _plan(repo, "a")
    assert _names(plan) == ["b", "a"]
    (branch,) = plan.optional_branches
    assert branch.available
    assert [i.name for i in branch.order] == ["x", "opt"]
    assert branch.edge.chain == ("a",)
    assert branch.target == PluginIdentity("opt", HOST)


def test_optional_branch_cannot_change_frozen_selection(virtual_ida_environment):
    repo = _repo(
        _zip("a", deps=["d==1.0.0", {"plugin": "opt", "required": False}]),
        _zip("d", "1.0.0"),
        _zip("d", "2.0.0"),
        _zip("opt", deps=["d==2.0.0"]),
    )
    plan = _plan(repo, "a")
    assert _node(plan, "d").version == "1.0.0"
    (branch,) = plan.optional_branches
    assert not branch.available
    assert "frozen by the required plan" in (branch.unavailable_reason or "")
    assert branch.diagnostics[0].kind == "conflict"


def test_optional_branch_may_upgrade_installed_plugin_outside_plan(virtual_ida_environment):
    install_plugin_archive(_zip("d", "1.0.0"), "d")
    repo = _repo(
        _zip("a", deps=[{"plugin": "opt", "required": False}]), _zip("opt", deps=["d==2.0.0"]), _zip("d", "2.0.0")
    )
    plan = _plan(repo, "a")
    (branch,) = plan.optional_branches
    assert branch.available
    d = branch.nodes[PluginIdentity("d", HOST)]
    assert d.operation == "upgrade"


def test_optional_branch_missing_target_is_unavailable_and_plan_succeeds(virtual_ida_environment):
    repo = _repo(_zip("a", deps=[{"plugin": "opt", "required": False}]))
    plan = _plan(repo, "a")
    (branch,) = plan.optional_branches
    assert not branch.available
    assert branch.diagnostics[0].kind == "unavailable"


def test_nested_optional_records_prerequisite_branch(virtual_ida_environment):
    repo = _repo(
        _zip("a", deps=[{"plugin": "opt1", "required": False}]),
        _zip("opt1", deps=[{"plugin": "opt2", "required": False}]),
        _zip("opt2"),
    )
    plan = _plan(repo, "a")
    assert [b.edge.spec.plugin for b in plan.optional_branches] == ["opt1", "opt2"]
    assert plan.optional_branches[0].prerequisite is None
    assert plan.optional_branches[1].prerequisite == 0
    assert plan.optional_branches[1].edge.chain == ("a", "opt1")


def test_node_reached_by_required_and_optional_edges(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["b", {"plugin": "b", "required": False}]), _zip("b"))
    plan = _plan(repo, "a")
    assert _names(plan) == ["b", "a"]
    (branch,) = plan.optional_branches
    assert branch.available
    assert branch.nodes == {}
    assert branch.diagnostics[0].kind == "satisfied"


def test_optional_edges_are_visited_once_across_branches(virtual_ida_environment):
    repo = _repo(
        _zip("a", deps=["b", {"plugin": "opt", "required": False}]),
        _zip("b", deps=[{"plugin": "opt", "required": False}]),
        _zip("opt"),
    )
    plan = _plan(repo, "a")
    assert [b.edge.chain for b in plan.optional_branches] == [("a", "b"), ("a",)]
    assert all(b.available for b in plan.optional_branches)


# ---------------------------------------------------------------------------
# suites and components
# ---------------------------------------------------------------------------


def test_dependency_targeting_planned_component_is_rejected(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["suite", "comp-a"]), _suite_zip("suite", "1.0.0", [("comp-a", "1.0.0", {})]))
    with pytest.raises(DependencyTargetsComponentError, match="suite 'suite'"):
        _plan(repo, "a")


def test_dependency_targeting_installed_component_is_rejected(virtual_ida_environment):
    install_plugin_archive(_suite_zip("suite", "1.0.0", [("comp-a", "1.0.0", {})]), "suite")
    repo = _repo(_zip("a", deps=["comp-a"]), _zip("comp-a"))
    with pytest.raises(DependencyTargetsComponentError, match="suite 'suite'"):
        _plan(repo, "a")


def test_component_dependencies_are_followed_with_component_chain(virtual_ida_environment):
    repo = _repo(_suite_zip("suite", "1.0.0", [("comp-a", "1.0.0", {"deps": ["x"]})]), _zip("x"))
    plan = _plan(repo, "suite")
    assert _names(plan) == ["x", "suite"]
    assert _node(plan, "x").chain == ("suite/comp-a", "x")


def test_planned_component_colliding_with_planned_plugin_conflicts(virtual_ida_environment):
    repo = _repo(
        _zip("a", deps=["comp-a", "suite"]), _zip("comp-a"), _suite_zip("suite", "1.0.0", [("comp-a", "1.0.0", {})])
    )
    with pytest.raises(DependencyConflictError, match="planned plugin"):
        _plan(repo, "a")


def test_planned_component_colliding_with_installed_plugin_conflicts(virtual_ida_environment):
    install_plugin_archive(_zip("comp-a"), "comp-a")
    repo = _repo(_zip("a", deps=["suite"]), _suite_zip("suite", "1.0.0", [("comp-a", "1.0.0", {})]))
    with pytest.raises(DependencyConflictError, match="installed plugin"):
        _plan(repo, "a")


def test_upgrading_installed_suite_does_not_collide_with_itself(virtual_ida_environment):
    install_plugin_archive(_suite_zip("suite", "1.0.0", [("comp-a", "1.0.0", {})]), "suite")
    repo = _repo(_suite_zip("suite", "2.0.0", [("comp-a", "2.0.0", {})]))
    plan = _plan(repo, "suite", upgrade=True)
    assert _node(plan, "suite").operation == "upgrade"


# ---------------------------------------------------------------------------
# metadata completeness
# ---------------------------------------------------------------------------


def test_complete_index_never_fetches_archives(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["b"], python_deps=["requests"]), _zip("b"))
    plan = _plan(repo, "a")
    assert all(isinstance(n.source, LocationSource) and n.source.artifact_sha256 is None for n in plan.nodes.values())
    assert plan.combined_python_requirements() == ["requests"]


def _incomplete_repo(tmp_path: Path, name: str) -> JSONFilePluginRepo:
    """An index entry published before expansion: inline Python deps left as a marker."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/ida-plugin.json", json.dumps(_manifest(name, python_deps="inline")))
        zf.writestr(f"{name}/{name}.py", "# /// script\n# dependencies = ['requests']\n# ///\n")
    zip_data = buf.getvalue()
    path = tmp_path / f"{name}.zip"
    path.write_bytes(zip_data)
    metadata = IDAMetadataDescriptor.model_validate(_manifest(name, python_deps="inline"))
    import hashlib

    location = PluginArchiveLocation(url=path.as_uri(), sha256=hashlib.sha256(zip_data).hexdigest(), metadata=metadata)
    return JSONFilePluginRepo([Plugin(name=name, host=HOST, versions={"1.0.0": [location]})])


def test_incomplete_index_entry_is_expanded_by_fetching_once(virtual_ida_environment, tmp_path):
    repo = _incomplete_repo(tmp_path, "a")
    context = _context(repo)
    plan = plan_install(context, [_root("a", repo)])
    a = _node(plan, "a")
    assert a.metadata.plugin.python_dependencies == ["requests"]
    assert isinstance(a.source, LocationSource) and a.source.artifact_sha256 in context.artifacts


def test_incomplete_index_entry_fails_in_index_only_mode(virtual_ida_environment, tmp_path):
    repo = _incomplete_repo(tmp_path, "a")
    with pytest.raises(IncompleteMetadataError, match="index-only"):
        plan_install(_context(repo, index_only=True), [_root("a", repo)])


# ---------------------------------------------------------------------------
# roots
# ---------------------------------------------------------------------------


def test_root_already_installed_without_upgrade_fails(virtual_ida_environment):
    install_plugin_archive(_zip("a"), "a")
    with pytest.raises(PluginAlreadyInstalledError):
        _plan(_repo(_zip("a", "2.0.0")), "a")


def test_root_upgrade_selects_newer_version(virtual_ida_environment):
    install_plugin_archive(_zip("a"), "a")
    plan = _plan(_repo(_zip("a", "2.0.0", deps=["b"]), _zip("b")), "a", upgrade=True)
    assert _node(plan, "a").operation == "upgrade"
    assert _names(plan) == ["b", "a"]


def test_root_upgrade_at_same_version_retains_and_repairs_dependencies(virtual_ida_environment, tmp_path):
    _install_then_remove_child(tmp_path, _zip("a", deps=["b"]), "a", "b")
    plan = _plan(_repo(_zip("a", deps=["b"]), _zip("b")), "a", upgrade=True)
    assert _node(plan, "a").operation == "retain"
    assert _node(plan, "b").operation == "install"


def test_root_pinned_downgrade_fails(virtual_ida_environment):
    install_plugin_archive(_zip("a", "2.0.0"), "a")
    with pytest.raises(PluginVersionDowngradeError):
        _plan(_repo(_zip("a", "1.0.0")), "a==1.0.0", upgrade=True)


def test_root_unpinned_lower_available_retains_with_warning(virtual_ida_environment):
    install_plugin_archive(_zip("a", "2.0.0"), "a")
    plan = _plan(_repo(_zip("a", "1.0.0")), "a", upgrade=True)
    assert _node(plan, "a").operation == "retain"
    assert plan.warnings and "keeping it" in plan.warnings[0]


def test_root_installed_from_other_host_conflicts(virtual_ida_environment):
    install_plugin_archive(_zip("a", host=OTHER_HOST), "a")
    with pytest.raises(InstalledPluginNameConflictError):
        _plan(_repo(_zip("a", "2.0.0")), f"a@{HOST}", upgrade=True)


def test_archive_root_uses_local_bytes(virtual_ida_environment):
    zip_data = _zip("a", deps=["b"])
    repo = _repo(_zip("b"))
    context = _context(repo)
    plan = plan_install(context, [ArchiveRoot(zip_data)])
    a = _node(plan, "a")
    assert isinstance(a.source, LocalArchiveSource)
    assert context.artifacts.get(a.source.sha256) == zip_data
    assert a.source.manifest_path == Path("a/ida-plugin.json")
    assert _names(plan) == ["b", "a"]


def test_archive_root_incompatible_platform_fails(virtual_ida_environment):
    with pytest.raises(PlatformIncompatibleError):
        plan_install(_context(None), [ArchiveRoot(_zip("a", platforms=[_other_platform()]))])


def test_editable_root_links_directory(virtual_ida_environment, tmp_path):
    plugin_dir = tmp_path / "a"
    plugin_dir.mkdir()
    (plugin_dir / "ida-plugin.json").write_text(json.dumps(_manifest("a", deps=["b"])))
    (plugin_dir / "a.py").write_text("# plugin")
    repo = _repo(_zip("b"))
    plan = plan_install(_context(repo), [EditableRoot(plugin_dir)])
    a = _node(plan, "a")
    assert a.operation == "editable"
    assert isinstance(a.source, EditableSource) and a.source.directory == plugin_dir.resolve()
    assert _names(plan) == ["b", "a"]


def test_location_root_uses_the_given_archive_location(virtual_ida_environment):
    repo = _repo(_zip("a", "1.0.0", deps=["b"]), _zip("a", "2.0.0"), _zip("b"))
    location = repo.find_plugin_from_spec("a==1.0.0")
    plan = plan_install(_context(repo), [LocationRoot(location, repo, repo_name="named")])
    a = _node(plan, "a")
    assert a.version == "1.0.0"
    assert isinstance(a.source, LocationSource) and a.source.location == location and a.source.repo_name == "named"
    assert _names(plan) == ["b", "a"]


def test_multiple_roots_share_selections(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["c"]), _zip("b", deps=["c"]), _zip("c"))
    plan = _plan(repo, "a", "b")
    assert _names(plan) == ["c", "a", "b"]
    assert plan.roots == [PluginIdentity("a", HOST), PluginIdentity("b", HOST)]


# ---------------------------------------------------------------------------
# repositories
# ---------------------------------------------------------------------------


def test_ambiguous_unqualified_dependency_is_unavailable(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["b"]), _zip("b"), _zip("b", host=OTHER_HOST))
    with pytest.raises(DependencyUnavailableError, match="qualify it"):
        _plan(repo, "a")


def test_qualified_dependency_selects_host(virtual_ida_environment):
    repo = _repo(_zip("a", deps=[f"b@{OTHER_HOST}"]), _zip("b"), _zip("b", host=OTHER_HOST))
    plan = _plan(repo, "a")
    assert _node(plan, "b").identity == PluginIdentity("b", OTHER_HOST)


def test_aggregate_repo_routes_fetch_to_owning_repository(virtual_ida_environment, tmp_path):
    _fs_repo(tmp_path / "first", _zip("a", deps=["b"]))
    _fs_repo(tmp_path / "second", _zip("b"))
    aggregate = AggregatePluginRepo(
        {
            "first": PluginRepository(name="first", url=(tmp_path / "first").as_uri(), reserved=False),
            "second": PluginRepository(name="second", url=(tmp_path / "second").as_uri(), reserved=False),
        }
    )
    plan = _plan(aggregate, "a")
    b = _node(plan, "b")
    assert isinstance(b.source, LocationSource)
    assert b.source.repo_name == "second"
    assert aggregate.describe_location_source(b.source.location) == "second"
    assert aggregate.fetch_location(b.source.location) == (tmp_path / "second" / "archive-0.zip").read_bytes()


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

API_KEY = {"key": "api_key", "type": "string", "required": True, "name": "API key"}
VERBOSE = {"key": "verbose", "type": "boolean", "required": True, "name": "Verbose", "default": False}
MODE = {"key": "mode", "type": "string", "required": True, "name": "Mode", "choices": ["fast", "slow"]}


def test_configuration_requirements_distinguish_root_and_dependency(virtual_ida_environment):
    repo = _repo(
        _suite_zip("pack", "1.0.0", [("comp", "1.0.0", {"settings": [MODE]})], settings=[API_KEY, VERBOSE]),
        _zip("dep", settings=[API_KEY]),
    )
    plan = _plan(
        _repo(_zip("pack", deps=["dep"], settings=[API_KEY, VERBOSE]), _zip("dep", settings=[API_KEY])), "pack"
    )
    assert [r.argument() for r in plan.missing_configuration()] == [
        "--dependency-config dep.api_key=<value>",
        "--config api_key=<value>",
    ]
    plan = _plan(repo, "pack")
    assert [r.argument() for r in plan.missing_configuration()] == [
        "--config api_key=<value>",
        "--config comp.mode=<value>",
    ]
    assert plan.missing_configuration()[1].chain == ("pack/comp",)


def test_stored_settings_satisfy_requirements_unless_invalid(virtual_ida_environment):
    from hcli.lib.ida import set_ida_config

    config = IDAConfigJson()
    config.plugins["pack"] = PluginConfig(settings={"api_key": "abc", "mode": "medium"})
    set_ida_config(config)
    plan = _plan(_repo(_zip("pack", settings=[API_KEY, MODE])), "pack")
    (requirement,) = plan.missing_configuration()
    assert requirement.key == "mode" and requirement.reason == "invalid" and requirement.current_value == "medium"


def test_retained_nodes_have_no_configuration_requirements(virtual_ida_environment):
    install_plugin_archive(_zip("dep", settings=[API_KEY]), "dep", config_values={("dep", "api_key"): "k"})
    plan = _plan(_repo(_zip("pack", deps=["dep"])), "pack")
    assert plan.missing_configuration() == []


def test_optional_branch_configuration_is_reported_per_branch(virtual_ida_environment):
    repo = _repo(_zip("pack", deps=[{"plugin": "opt", "required": False}]), _zip("opt", settings=[API_KEY]))
    plan = _plan(repo, "pack")
    assert plan.missing_configuration() == []
    (requirement,) = plan.missing_configuration(branches=[0])
    assert requirement.argument() == "--dependency-config opt.api_key=<value>"
    assert requirement.branch == 0


def test_apply_configuration_values_validates_and_parses(virtual_ida_environment):
    repo = _repo(_zip("pack", deps=["dep"], settings=[API_KEY, VERBOSE]), _zip("dep", settings=[MODE]))
    plan = _plan(repo, "pack")
    plan.apply_configuration_values({("Pack", "api_key"): "abc", ("pack", "verbose"): "true", ("dep", "mode"): "fast"})
    assert plan.missing_configuration() == []
    assert plan.configuration_values[("pack", "verbose")] is True
    with pytest.raises(ValueError, match="unknown plugin"):
        plan.apply_configuration_values({("nope", "api_key"): "x"})
    with pytest.raises(ValueError, match="unknown setting"):
        plan.apply_configuration_values({("pack", "nope"): "x"})
    with pytest.raises(ValueError, match="invalid value"):
        plan.apply_configuration_values({("dep", "mode"): "medium"})


def test_combined_python_requirements_dedupe_in_order(virtual_ida_environment):
    _place_installed(_zip("kept", python_deps=["pyyaml"]), "kept")
    repo = _repo(
        _suite_zip(
            "pack",
            "1.0.0",
            [("comp", "1.0.0", {"python_deps": ["rich"]})],
            deps=["dep", "kept", {"plugin": "opt", "required": False}],
            python_deps=["requests"],
        ),
        _zip("dep", python_deps=["numpy", "requests"]),
        _zip("opt", python_deps=["click"]),
    )
    plan = _plan(repo, "pack")
    assert plan.combined_python_requirements() == ["numpy", "requests", "pyyaml", "rich"]
    assert plan.combined_python_requirements(branches=[0]) == ["numpy", "requests", "pyyaml", "rich", "click"]


def test_component_settings_target_is_qualified_when_ambiguous(virtual_ida_environment):
    repo = _repo(
        _zip("pack", deps=[{"plugin": "suite-a", "required": False}, {"plugin": "suite-b", "required": False}]),
        _suite_zip("suite-a", "1.0.0", [("comp", "1.0.0", {"settings": [MODE]})]),
        _suite_zip("suite-b", "1.0.0", [("comp", "1.0.0", {"settings": [MODE]})]),
    )
    plan = _plan(repo, "pack")

    with pytest.raises(
        ValueError, match=r"ambiguous configuration target 'comp'.*suite-a/comp\.mode.*suite-b/comp\.mode"
    ):
        plan.apply_configuration_values({("comp", "mode"): "fast"})

    plan.apply_configuration_values({("suite-a/comp", "mode"): "fast", ("Suite-B/Comp", "mode"): "slow"})
    assert plan.missing_configuration(plan.all_branches()) == []


def test_unambiguous_component_settings_target_accepts_bare_name(virtual_ida_environment):
    repo = _repo(_suite_zip("pack", "1.0.0", [("comp", "1.0.0", {"settings": [MODE]})]))
    plan = _plan(repo, "pack")
    plan.apply_configuration_values({("comp", "mode"): "fast"})
    plan.apply_configuration_values({("pack/comp", "mode"): "fast"})
    assert plan.missing_configuration() == []


def test_root_not_in_repository_is_reported_as_plugin_not_found(virtual_ida_environment):
    repo = _repo(_zip("a", deps=["missing"]))

    with pytest.raises(PluginNotFoundError, match="plugin 'ghost' was not found") as excinfo:
        _plan(repo, "ghost")
    assert isinstance(excinfo.value, DependencyUnavailableError)
    assert "required by" not in str(excinfo.value)

    with pytest.raises(DependencyUnavailableError, match="dependency 'missing' is unavailable \\(required by a\\)"):
        _plan(repo, "a")


def test_combined_python_requirements_include_untouched_installed_plugins(virtual_ida_environment):
    _place_installed(_zip("other", python_deps=["packaging==24.0"]), "other")
    _place_installed(_zip("kept", "1.0.0", python_deps=["pyyaml"]), "kept")
    repo = _repo(
        _zip("a", deps=["kept"], python_deps=["packaging==25.0"]),
        _zip("kept", "2.0.0", python_deps=["pyyaml>=6"]),
    )
    plan = _plan(repo, "a")
    assert plan.mutating_python_requirements() == ["packaging==25.0"]
    assert plan.combined_python_requirements() == ["pyyaml", "packaging==25.0", "packaging==24.0"]
    assert plan.installed_python_requirements() == {"other": ["packaging==24.0"]}


def test_combined_python_requirements_include_installed_components(virtual_ida_environment):
    _place_installed(
        _suite_zip("suite", "1.0.0", [("comp", "1.0.0", {"python_deps": ["rich"]})], python_deps=["click"]), "suite"
    )
    repo = _repo(_zip("a", python_deps=["requests"]))
    plan = _plan(repo, "a")
    assert plan.combined_python_requirements() == ["requests", "click", "rich"]


def test_mutating_python_requirements_cover_only_installed_or_upgraded_nodes(virtual_ida_environment):
    _place_installed(_zip("kept", python_deps=["pyyaml"]), "kept")
    repo = _repo(
        _zip("a", deps=["kept", {"plugin": "opt", "required": False}]),
        _zip("opt", python_deps=["click"]),
    )
    plan = _plan(repo, "a")
    assert _names(plan) == ["kept", "a"]
    assert plan.mutating_python_requirements() == []
    assert plan.mutating_python_requirements(branches=[0]) == ["click"]
    assert plan.combined_python_requirements() == ["pyyaml"]


def test_unreadable_installed_component_tree_blocks_only_plans_with_python_work(virtual_ida_environment):
    _place_installed(_suite_zip("suite", "1.0.0", [("comp", "1.0.0", {"python_deps": ["pyyaml"]})]), "suite")
    (get_plugins_directory() / "suite" / "comp" / "ida-plugin.json").write_text("{not json")
    repo = _repo(
        _zip("a"),
        _zip("b", python_deps=["requests"]),
        _suite_zip("suite", "2.0.0", [("comp", "2.0.0", {})]),
    )

    plan = _plan(repo, "a")
    assert _names(plan) == ["a"]
    assert plan.mutating_python_requirements() == []
    with pytest.raises(BrokenPluginInstallationError, match="suite"):
        plan.combined_python_requirements()

    plan = _plan(repo, "b")
    assert plan.mutating_python_requirements() == ["requests"]
    with pytest.raises(BrokenPluginInstallationError, match="suite"):
        plan.combined_python_requirements()

    plan = _plan(repo, "suite", upgrade=True)
    assert _names(plan) == ["suite"]
    assert plan.installed_python_requirements() == {}
