"""Shared install/upgrade CLI flow helpers."""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from fixtures import *
from test_plugin_resolve import _fs_repo, _manifest, _plan, _repo, _suite_zip, _zip

from hcli.commands.plugin import plugin as plugin_group
from hcli.commands.plugin._install_flow import (
    _group_by_target,
    find_dropped_dependencies,
    parse_configuration_arguments,
    render_dependency_chain,
    take_root_requirements,
)
from hcli.lib.ida.plugin import IDAMetadataDescriptor
from hcli.lib.ida.plugin.install import is_plugin_installed

_TOKEN = [{"key": "token", "type": "string", "name": "Token", "documentation": "d", "required": True}]


def _descriptor(name: str, deps: list) -> IDAMetadataDescriptor:
    return IDAMetadataDescriptor.model_validate(_manifest(name, "1.0.0", deps=deps))


def test_dropped_dependencies_compare_full_specs():
    old = _descriptor("pack", ["a==1.0.0", "b", "c@https://github.com/x/c", "d"])
    new = _descriptor("pack", ["a==2.0.0", "b", "c@https://github.com/y/c"])
    removed, changed = find_dropped_dependencies(old, new)
    assert removed == ["d"]
    assert changed == ["a==1.0.0 -> a==2.0.0", "c@https://github.com/x/c -> c@https://github.com/y/c"]


def test_dropped_dependencies_ignore_unchanged_and_case():
    old = _descriptor("pack", ["Alpha==1.0.0"])
    new = _descriptor("pack", ["Alpha==1.0.0"])
    assert find_dropped_dependencies(old, new) == ([], [])


def test_render_dependency_chain_walks_from_root(virtual_ida_environment):
    repo = _repo(_zip("root", deps=["mid"]), _zip("mid", deps=["leaf"]), _zip("leaf"))
    plan = _plan(repo, "root")
    assert render_dependency_chain(plan, "leaf") == "root -> mid -> leaf"
    assert render_dependency_chain(plan, "root") == "root"
    assert render_dependency_chain(plan, "unknown") == "unknown"


def test_render_dependency_chain_covers_optional_branches(virtual_ida_environment):
    repo = _repo(_zip("root", deps=[{"plugin": "opt", "required": False}]), _zip("opt", deps=["leaf"]), _zip("leaf"))
    plan = _plan(repo, "root")
    assert render_dependency_chain(plan, "LEAF") == "root -> opt -> leaf"


def test_dependency_config_for_unavailable_optional_is_ignored_with_notice(virtual_ida_environment):
    repo = _repo(
        _zip("a", deps=[{"plugin": "ghost", "required": False}, {"plugin": "b", "required": False}]),
        _zip("b", settings=_TOKEN),
    )
    plan = _plan(repo, "a")

    parsed = parse_configuration_arguments(plan, "a", [], ["ghost.token=1", "b.token=2"])

    assert parsed.values == {("b", "token"): "2"}
    assert parsed.ignored == ["ghost.token=1"]

    parsed = parse_configuration_arguments(plan, "a", [], ["nobody.token=1"])
    assert parsed.values == {("nobody", "token"): "1"}
    with pytest.raises(ValueError, match="nobody"):
        plan.apply_configuration_values(parsed.values)


def test_take_root_requirements_separates_root_and_components_from_dependencies(virtual_ida_environment):
    repo = _repo(
        _suite_zip("pack", "1.0.0", [("comp", "1.0.0", {"settings": _TOKEN})], deps=["b"], settings=_TOKEN),
        _zip("b", settings=_TOKEN),
    )
    plan = _plan(repo, "pack")
    requirements = _group_by_target(plan.missing_configuration())
    assert set(requirements) == {"pack", "comp", "b"}

    taken = take_root_requirements(plan.nodes[plan.roots[0]], requirements)

    assert set(taken) == {"pack", "comp"}
    assert set(requirements) == {"b"}


def test_upgrade_cli_reports_dependencies_dropped_by_a_component(virtual_ida_environment, tmp_path):
    repo_dir = tmp_path / "repo"
    _fs_repo(
        repo_dir,
        _zip("lib"),
        _suite_zip("tools", "1.0.0", [("tool-a", "1.0.0", {"deps": ["lib"]})]),
        _suite_zip("tools", "2.0.0", [("tool-a", "2.0.0", {})]),
    )
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "install", "tools==1.0.0"])
    assert result.exit_code == 0, result.output
    assert is_plugin_installed("lib")

    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "upgrade", "tools"])

    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())
    assert "Upgraded plugin: tools==2.0.0" in output
    assert "dependencies were removed from tools" in output
    assert "lib" in output and "remain installed" in output


def test_dropped_dependencies_report_required_flag_changes():
    old = _descriptor("pack", ["b", {"plugin": "c", "required": False}])
    new = _descriptor("pack", [{"plugin": "b", "required": False}, "c"])
    removed, changed = find_dropped_dependencies(old, new)
    assert removed == []
    assert changed == ["b -> b (optional)", "c (optional) -> c"]
