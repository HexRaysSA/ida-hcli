"""Shared install/upgrade CLI flow helpers."""

from __future__ import annotations

from fixtures import *
from test_plugin_resolve import _manifest, _plan, _repo, _zip

from hcli.commands.plugin._install_flow import find_dropped_dependencies, render_dependency_chain
from hcli.lib.ida.plugin import IDAMetadataDescriptor


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
