"""Tests for dependency-aware uninstall."""

from __future__ import annotations

import pytest
from click.testing import CliRunner
from fixtures import *
from test_plugin_resolve import HOST, _fs_repo, _suite_zip, _zip

from hcli.commands.plugin import plugin as plugin_group
from hcli.lib.ida.plugin.components import find_root_manifest_in_archive
from hcli.lib.ida.plugin.dependents import (
    expand_installed_record,
    find_companions,
    find_dependents,
    find_remaining_declarers,
)
from hcli.lib.ida.plugin.install import (
    find_installed_plugin,
    get_installed_plugin_records,
    get_plugins_directory,
    install_plugin_archive,
    is_plugin_installed,
)


def _install(tmp_path, *archives: bytes) -> None:
    repo = _fs_repo(tmp_path / "repo", *archives)
    for archive in archives:
        _, metadata = find_root_manifest_in_archive(archive)
        if not is_plugin_installed(metadata.plugin.name):
            install_plugin_archive(archive, metadata.plugin.name, plugin_repo=repo, check_environment=False)


def _uninstall(*args: str):
    return CliRunner(mix_stderr=False).invoke(plugin_group, ["uninstall", *args])


def test_find_dependents_reports_required_and_optional(virtual_ida_environment, tmp_path):
    _install(
        tmp_path,
        _zip("lib"),
        _zip("needs-lib", deps=["lib"]),
        _zip("likes-lib", deps=[{"plugin": "lib", "required": False}]),
    )
    records = get_installed_plugin_records()

    dependents = find_dependents(records, find_installed_plugin("lib"))

    assert {(d.declarer, d.spec.required) for d in dependents} == {("needs-lib", True), ("likes-lib", False)}


def test_find_dependents_sees_component_declarations_and_targets(virtual_ida_environment, tmp_path):
    _install(
        tmp_path,
        _suite_zip("lib-suite", "1.0.0", [("lib-core", "1.0.0", {})]),
        _suite_zip("tools", "1.0.0", [("tool-a", "1.0.0", {"deps": ["lib-suite==1.0.0"]})]),
    )
    records = get_installed_plugin_records()

    dependents = find_dependents(records, find_installed_plugin("lib-suite"))

    assert [(d.owner.name, d.declarer, d.target) for d in dependents] == [("tools", "tool-a", "lib-suite")]
    assert dependents[0].describe_declarer() == "tool-a (component of tools)"


def test_find_companions_includes_component_declarations_only_when_installed(virtual_ida_environment, tmp_path):
    _install(
        tmp_path,
        _suite_zip(
            "pack",
            "1.0.0",
            [("pack-a", "1.0.0", {"deps": ["helper", {"plugin": "absent", "required": False}]})],
            deps=["shared"],
        ),
        _zip("helper"),
        _zip("shared"),
    )
    records = get_installed_plugin_records()

    companions = find_companions(records, find_installed_plugin("pack"))

    assert sorted(c.name for c in companions) == ["helper", "shared"]


def test_uninstall_prints_dependent_notices(virtual_ida_environment, tmp_path):
    _install(
        tmp_path,
        _zip("lib"),
        _zip("needs-lib", deps=["lib"]),
        _zip("likes-lib", deps=[{"plugin": "lib", "required": False}]),
    )

    result = _uninstall("--yes", "lib")

    assert result.exit_code == 0, result.output
    assert "needs-lib" in result.output and "requires" in result.output
    assert "likes-lib" in result.output and "optionally" in result.output
    assert not is_plugin_installed("lib")
    assert is_plugin_installed("needs-lib")


def test_uninstall_notice_names_dependent_component(virtual_ida_environment, tmp_path):
    _install(
        tmp_path,
        _zip("lib"),
        _suite_zip("tools", "1.0.0", [("tool-a", "1.0.0", {"deps": ["lib"]})]),
    )

    result = _uninstall("--yes", "lib")

    assert result.exit_code == 0, result.output
    assert "tool-a" in result.output
    assert "tools" in result.output


def test_uninstall_keeps_companion_still_declared_elsewhere(virtual_ida_environment, tmp_path):
    _install(tmp_path, _zip("lib"), _zip("pack", deps=["lib"]), _zip("other", deps=["lib"]))

    result = _uninstall("--yes", "pack")

    assert result.exit_code == 0, result.output
    assert not is_plugin_installed("pack")
    assert is_plugin_installed("lib")
    assert "other" in result.output


def test_uninstall_keeps_companion_declared_by_installed_component(virtual_ida_environment, tmp_path):
    _install(
        tmp_path,
        _zip("lib"),
        _zip("pack", deps=["lib"]),
        _suite_zip("tools", "1.0.0", [("tool-a", "1.0.0", {"deps": ["lib"]})]),
    )

    result = _uninstall("--yes", "pack")

    assert result.exit_code == 0, result.output
    assert is_plugin_installed("lib")
    assert "tool-a" in result.output


def test_uninstall_removes_companion_nothing_else_declares(virtual_ida_environment, tmp_path):
    _install(tmp_path, _zip("lib"), _zip("pack", deps=["lib"]))

    result = _uninstall("--yes", "pack")

    assert result.exit_code == 0, result.output
    assert not is_plugin_installed("pack")
    assert not is_plugin_installed("lib")


def test_uninstall_does_not_collect_grandchild_companions(virtual_ida_environment, tmp_path):
    _install(tmp_path, _zip("leaf"), _zip("mid", deps=["leaf"]), _zip("pack", deps=["mid"]))

    result = _uninstall("--yes", "pack")

    assert result.exit_code == 0, result.output
    assert not is_plugin_installed("mid")
    assert is_plugin_installed("leaf")


def test_find_remaining_declarers_after_removal(virtual_ida_environment, tmp_path):
    _install(tmp_path, _zip("lib"), _zip("pack", deps=["lib"]), _zip("other", deps=[f"lib=={'1.0.0'}@{HOST}"]))
    records = get_installed_plugin_records()
    remaining = [r for r in records if r.name != "pack"]

    declarers = find_remaining_declarers(remaining, find_installed_plugin("lib"))

    assert [d.declarer for d in declarers] == ["other"]


def _corrupt_component_manifest(suite: str, component: str) -> None:
    (get_plugins_directory() / suite / component / "ida-plugin.json").write_text("{not json")


def test_expand_installed_record_raises_on_broken_component_tree(virtual_ida_environment, tmp_path):
    _install(tmp_path, _suite_zip("suite", "1.0.0", [("comp", "1.0.0", {})]))
    _corrupt_component_manifest("suite", "comp")

    with pytest.raises(ValueError, match="comp"):
        expand_installed_record(find_installed_plugin("suite"))


def test_find_dependents_reports_broken_trees_it_could_not_inspect(virtual_ida_environment, tmp_path):
    _install(tmp_path, _zip("lib"), _suite_zip("suite", "1.0.0", [("comp", "1.0.0", {"deps": ["lib"]})]))
    records = get_installed_plugin_records()
    assert [d.declarer for d in find_dependents(records, find_installed_plugin("lib"))] == ["comp"]

    _corrupt_component_manifest("suite", "comp")
    broken: list[str] = []

    dependents = find_dependents(records, find_installed_plugin("lib"), broken)

    assert dependents == []
    assert len(broken) == 1 and broken[0].startswith("suite: ")


def test_uninstall_warns_about_uninspectable_components(virtual_ida_environment, tmp_path):
    _install(tmp_path, _zip("lib"), _suite_zip("suite", "1.0.0", [("comp", "1.0.0", {"deps": ["lib"]})]))
    _corrupt_component_manifest("suite", "comp")

    result = _uninstall("--yes", "lib")

    assert result.exit_code == 0, result.output
    assert result.output.count("could not inspect components of suite") == 1
    assert not is_plugin_installed("lib")


def test_uninstall_keeps_companion_when_a_declarer_tree_cannot_be_inspected(virtual_ida_environment, tmp_path):
    _install(
        tmp_path,
        _zip("lib"),
        _zip("pack", deps=["lib"]),
        _suite_zip("suite", "1.0.0", [("comp", "1.0.0", {"deps": ["lib"]})]),
    )
    _corrupt_component_manifest("suite", "comp")

    result = _uninstall("--yes", "pack")

    assert result.exit_code == 0, result.output
    assert not is_plugin_installed("pack")
    assert is_plugin_installed("lib")
    assert "kept: could not inspect components of suite" in " ".join(result.output.split())


def test_host_qualified_declarations_ignore_plugins_from_another_host(virtual_ida_environment, tmp_path):
    from test_plugin_resolve import OTHER_HOST

    _install(tmp_path, _zip("dep", host=OTHER_HOST), _zip("app", deps=[{"plugin": f"dep@{HOST}", "required": False}]))
    records = get_installed_plugin_records()
    dep = find_installed_plugin("dep")
    app = find_installed_plugin("app")

    assert find_dependents(records, dep) == []
    assert find_companions(records, app) == []
    assert find_remaining_declarers([app], dep) == []

    unqualified = _zip("app2", deps=["dep"])
    _install(tmp_path, unqualified)
    records = get_installed_plugin_records()
    assert [d.declarer for d in find_dependents(records, dep)] == ["app2"]
    assert [c.name for c in find_companions(records, find_installed_plugin("app2"))] == ["dep"]
