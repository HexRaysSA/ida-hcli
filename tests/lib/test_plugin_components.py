"""Tests for tightly-coupled plugin components (suites)."""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import *
from pydantic import ValidationError

from hcli.commands.plugin import plugin as plugin_group
from hcli.lib.ida.plugin import IDAMetadataDescriptor, get_metadatas_with_paths_from_plugin_archive
from hcli.lib.ida.plugin.components import (
    check_component_name_collisions,
    collect_all_component_names_from_archive,
    find_root_manifest_in_archive,
    find_suite_for_component,
    walk_component_tree_from_archive,
    walk_component_tree_from_directory,
)
from hcli.lib.ida.plugin.install import (
    get_installed_plugin_records,
    install_plugin_archive,
    is_plugin_installed,
    uninstall_plugin,
    upgrade_plugin_archive,
)

logger = logging.getLogger(__name__)

HOST = "https://github.com/test/test-suite"


def _make_plugin_metadata(
    name: str,
    version: str,
    *,
    components: list[str] | None = None,
    deps: list[str] | None = None,
) -> dict:
    plugin: dict = {
        "name": name,
        "version": version,
        "entryPoint": f"{name}.py",
        "urls": {"repository": HOST},
        "authors": [{"name": "Test", "email": "test@example.com"}],
    }
    if components is not None:
        plugin["components"] = components
    if deps is not None:
        plugin["dependencies"] = deps
    return {"IDAMetadataDescriptorVersion": 1, "plugin": plugin}


def _make_standalone_zip(name: str, version: str, **kwargs) -> bytes:
    buf = io.BytesIO()
    metadata = _make_plugin_metadata(name, version, **kwargs)
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/ida-plugin.json", json.dumps(metadata))
        zf.writestr(f"{name}/{name}.py", "# plugin")
    return buf.getvalue()


def _make_suite_zip(
    suite_name: str,
    suite_version: str,
    components: list[tuple[str, str]],
    *,
    suite_deps: list[str] | None = None,
) -> bytes:
    buf = io.BytesIO()
    comp_names = [name for name, _ in components]
    suite_meta = _make_plugin_metadata(suite_name, suite_version, components=comp_names, deps=suite_deps)

    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{suite_name}/ida-plugin.json", json.dumps(suite_meta))
        zf.writestr(f"{suite_name}/{suite_name}.py", "# suite entry point")

        for comp_name, comp_version in components:
            comp_meta = _make_plugin_metadata(comp_name, comp_version)
            zf.writestr(f"{suite_name}/{comp_name}/ida-plugin.json", json.dumps(comp_meta))
            zf.writestr(f"{suite_name}/{comp_name}/{comp_name}.py", "# component")

    return buf.getvalue()


def _make_nested_suite_zip(
    suite_name: str,
    suite_version: str,
    components: list[tuple[str, str, list[tuple[str, str]]]],
) -> bytes:
    """Create a suite with nested components (components that have their own components)."""
    buf = io.BytesIO()
    top_comp_names = [name for name, _, _ in components]
    suite_meta = _make_plugin_metadata(suite_name, suite_version, components=top_comp_names)

    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{suite_name}/ida-plugin.json", json.dumps(suite_meta))
        zf.writestr(f"{suite_name}/{suite_name}.py", "# suite")

        for comp_name, comp_version, sub_components in components:
            sub_names = [n for n, _ in sub_components]
            comp_meta = _make_plugin_metadata(comp_name, comp_version, components=sub_names if sub_names else None)
            zf.writestr(f"{suite_name}/{comp_name}/ida-plugin.json", json.dumps(comp_meta))
            zf.writestr(f"{suite_name}/{comp_name}/{comp_name}.py", "# component")

            for sub_name, sub_version in sub_components:
                sub_meta = _make_plugin_metadata(sub_name, sub_version)
                zf.writestr(f"{suite_name}/{comp_name}/{sub_name}/ida-plugin.json", json.dumps(sub_meta))
                zf.writestr(f"{suite_name}/{comp_name}/{sub_name}/{sub_name}.py", "# sub-component")

    return buf.getvalue()


# ---------------------------------------------------------------------------
# PluginMetadata.components field
# ---------------------------------------------------------------------------


def test_metadata_with_components():
    data = _make_plugin_metadata("my-suite", "1.0.0", components=["comp-a", "comp-b"])
    descriptor = IDAMetadataDescriptor.model_validate(data)
    assert descriptor.plugin.components == ["comp-a", "comp-b"]


def test_metadata_without_components():
    data = _make_plugin_metadata("my-suite", "1.0.0")
    descriptor = IDAMetadataDescriptor.model_validate(data)
    assert descriptor.plugin.components == []


def test_metadata_empty_components():
    data = _make_plugin_metadata("my-suite", "1.0.0", components=[])
    descriptor = IDAMetadataDescriptor.model_validate(data)
    assert descriptor.plugin.components == []


def test_metadata_rejects_version_pin_in_components():
    data = _make_plugin_metadata("my-suite", "1.0.0", components=["comp-a==1.0.0"])
    with pytest.raises(ValidationError, match="version pins"):
        IDAMetadataDescriptor.model_validate(data)


def test_metadata_rejects_host_qualifier_in_components():
    data = _make_plugin_metadata("my-suite", "1.0.0", components=["comp-a@https://github.com/x/y"])
    with pytest.raises(ValidationError, match="host qualifiers"):
        IDAMetadataDescriptor.model_validate(data)


def test_metadata_rejects_duplicate_component_names():
    data = _make_plugin_metadata("my-suite", "1.0.0", components=["comp-a", "comp-a"])
    with pytest.raises(ValidationError, match="unique"):
        IDAMetadataDescriptor.model_validate(data)


def test_metadata_rejects_invalid_component_name():
    data = _make_plugin_metadata("my-suite", "1.0.0", components=["bad name!"])
    with pytest.raises(ValidationError):
        IDAMetadataDescriptor.model_validate(data)


def test_metadata_serialization_includes_components():
    data = _make_plugin_metadata("my-suite", "1.0.0", components=["comp-a", "comp-b"])
    descriptor = IDAMetadataDescriptor.model_validate(data)
    serialized = descriptor.model_dump(mode="json", by_alias=True)
    assert serialized["plugin"]["components"] == ["comp-a", "comp-b"]


# ---------------------------------------------------------------------------
# find_root_manifest_in_archive
# ---------------------------------------------------------------------------


def test_find_root_manifest_single_plugin():
    zip_data = _make_standalone_zip("my-plugin", "1.0.0")
    path, meta = find_root_manifest_in_archive(zip_data)
    assert meta.plugin.name == "my-plugin"


def test_find_root_manifest_in_suite():
    zip_data = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0"), ("comp-b", "2.0.0")])
    path, meta = find_root_manifest_in_archive(zip_data)
    assert meta.plugin.name == "my-suite"


def test_find_root_manifest_rejects_ambiguous():
    buf = io.BytesIO()
    meta_a = _make_plugin_metadata("plugin-a", "1.0.0")
    meta_b = _make_plugin_metadata("plugin-b", "1.0.0")
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin-a/ida-plugin.json", json.dumps(meta_a))
        zf.writestr("plugin-a/plugin-a.py", "# a")
        zf.writestr("plugin-b/ida-plugin.json", json.dumps(meta_b))
        zf.writestr("plugin-b/plugin-b.py", "# b")
    with pytest.raises(ValueError, match="multiple unrelated"):
        find_root_manifest_in_archive(buf.getvalue())


# ---------------------------------------------------------------------------
# walk_component_tree_from_archive
# ---------------------------------------------------------------------------


def test_walk_archive_flat_suite():
    zip_data = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0"), ("comp-b", "2.0.0")])
    path, meta = find_root_manifest_in_archive(zip_data)
    tree = walk_component_tree_from_archive(zip_data, path, meta)
    names = {m.plugin.name for _, m in tree}
    assert names == {"comp-a", "comp-b"}


def test_walk_archive_nested_suite():
    zip_data = _make_nested_suite_zip(
        "my-suite", "1.0.0",
        [("comp-a", "1.0.0", [("sub-x", "0.1.0")]), ("comp-b", "2.0.0", [])],
    )
    path, meta = find_root_manifest_in_archive(zip_data)
    tree = walk_component_tree_from_archive(zip_data, path, meta)
    names = {m.plugin.name for _, m in tree}
    assert names == {"comp-a", "comp-b", "sub-x"}


def test_collect_all_component_names():
    zip_data = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0"), ("comp-b", "2.0.0")])
    path, meta = find_root_manifest_in_archive(zip_data)
    names = collect_all_component_names_from_archive(zip_data, path, meta)
    assert names == {"comp-a", "comp-b"}


# ---------------------------------------------------------------------------
# Install suite
# ---------------------------------------------------------------------------


def test_install_suite(virtual_ida_environment):
    zip_data = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0"), ("comp-b", "2.0.0")])
    install_plugin_archive(zip_data, "my-suite")

    assert is_plugin_installed("my-suite")
    assert not is_plugin_installed("comp-a")
    assert not is_plugin_installed("comp-b")

    records = get_installed_plugin_records()
    names = {r.name for r in records}
    assert "my-suite" in names
    assert "comp-a" not in names
    assert "comp-b" not in names


def test_install_suite_components_on_disk(virtual_ida_environment):
    zip_data = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0"), ("comp-b", "2.0.0")])
    install_plugin_archive(zip_data, "my-suite")

    from hcli.lib.ida.plugin.install import get_plugin_directory

    suite_dir = get_plugin_directory("my-suite")
    assert (suite_dir / "comp-a" / "ida-plugin.json").exists()
    assert (suite_dir / "comp-b" / "ida-plugin.json").exists()


def test_walk_installed_suite_components(virtual_ida_environment):
    zip_data = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0"), ("comp-b", "2.0.0")])
    install_plugin_archive(zip_data, "my-suite")

    from hcli.lib.ida.plugin.install import get_plugin_directory

    suite_dir = get_plugin_directory("my-suite")
    tree = walk_component_tree_from_directory(suite_dir)
    names = {m.plugin.name for _, m in tree}
    assert names == {"comp-a", "comp-b"}


# ---------------------------------------------------------------------------
# Uninstall suite
# ---------------------------------------------------------------------------


def test_uninstall_suite_removes_components(virtual_ida_environment):
    zip_data = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0")])
    install_plugin_archive(zip_data, "my-suite")
    assert is_plugin_installed("my-suite")

    uninstall_plugin("my-suite")
    assert not is_plugin_installed("my-suite")

    from hcli.lib.ida.plugin.install import get_plugin_directory

    suite_dir = get_plugin_directory("my-suite")
    assert not suite_dir.exists()


def test_uninstall_component_refused_via_cli(virtual_ida_environment):
    zip_data = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0")])
    install_plugin_archive(zip_data, "my-suite")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["uninstall", "comp-a"])

    assert result.exit_code != 0
    assert "component of" in result.output.lower() or "component" in result.output.lower()
    assert is_plugin_installed("my-suite")


def test_uninstall_suite_lists_components_via_cli(virtual_ida_environment):
    zip_data = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0"), ("comp-b", "2.0.0")])
    install_plugin_archive(zip_data, "my-suite")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["uninstall", "--yes", "my-suite"])

    assert result.exit_code == 0, result.output
    assert not is_plugin_installed("my-suite")


# ---------------------------------------------------------------------------
# Upgrade suite
# ---------------------------------------------------------------------------


def test_upgrade_suite_replaces_components(virtual_ida_environment):
    v1 = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0")])
    v2 = _make_suite_zip("my-suite", "2.0.0", [("comp-a", "1.1.0"), ("comp-b", "1.0.0")])

    install_plugin_archive(v1, "my-suite")
    upgrade_plugin_archive(v2, "my-suite")

    from hcli.lib.ida.plugin.install import get_plugin_directory

    suite_dir = get_plugin_directory("my-suite")
    tree = walk_component_tree_from_directory(suite_dir)
    names = {m.plugin.name for _, m in tree}
    assert names == {"comp-a", "comp-b"}


def test_upgrade_suite_drops_removed_component(virtual_ida_environment):
    v1 = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0"), ("comp-b", "1.0.0")])
    v2 = _make_suite_zip("my-suite", "2.0.0", [("comp-a", "1.1.0")])

    install_plugin_archive(v1, "my-suite")

    from hcli.lib.ida.plugin.install import get_plugin_directory

    suite_dir = get_plugin_directory("my-suite")
    assert (suite_dir / "comp-b" / "ida-plugin.json").exists()

    upgrade_plugin_archive(v2, "my-suite")
    assert not (suite_dir / "comp-b").exists()
    tree = walk_component_tree_from_directory(suite_dir)
    names = {m.plugin.name for _, m in tree}
    assert names == {"comp-a"}


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------


def test_status_shows_component_count(virtual_ida_environment):
    zip_data = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0"), ("comp-b", "2.0.0")])
    install_plugin_archive(zip_data, "my-suite")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["status", "--skip-upgrade-check"])

    assert result.exit_code == 0, result.output
    assert "my-suite" in result.output
    assert "2 components" in result.output
    assert "comp-a" not in result.output
    assert "comp-b" not in result.output


def test_status_show_components_flag(virtual_ida_environment):
    zip_data = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0"), ("comp-b", "2.0.0")])
    install_plugin_archive(zip_data, "my-suite")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["status", "--skip-upgrade-check", "--show-components"])

    assert result.exit_code == 0, result.output
    assert "my-suite" in result.output
    assert "comp-a" in result.output
    assert "comp-b" in result.output


def test_status_json_includes_components(virtual_ida_environment):
    zip_data = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0"), ("comp-b", "2.0.0")])
    install_plugin_archive(zip_data, "my-suite")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["status", "--skip-upgrade-check", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    suite_entry = next(e for e in data["plugins"] if e["name"] == "my-suite")
    assert "components" in suite_entry
    comp_names = {c["name"] for c in suite_entry["components"]}
    assert comp_names == {"comp-a", "comp-b"}


# ---------------------------------------------------------------------------
# Name collision checking
# ---------------------------------------------------------------------------


def test_collision_component_vs_toplevel(virtual_ida_environment):
    standalone = _make_standalone_zip("comp-a", "1.0.0")
    install_plugin_archive(standalone, "comp-a")

    suite_zip = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0")])
    path, meta = find_root_manifest_in_archive(suite_zip)
    component_names = collect_all_component_names_from_archive(suite_zip, path, meta)

    collisions = check_component_name_collisions(component_names)
    assert len(collisions) == 1
    assert "comp-a" in collisions[0]


def test_collision_component_vs_other_suite(virtual_ida_environment):
    suite1 = _make_suite_zip("suite-1", "1.0.0", [("shared-comp", "1.0.0")])
    install_plugin_archive(suite1, "suite-1")

    suite2_zip = _make_suite_zip("suite-2", "1.0.0", [("shared-comp", "2.0.0")])
    path, meta = find_root_manifest_in_archive(suite2_zip)
    component_names = collect_all_component_names_from_archive(suite2_zip, path, meta)

    collisions = check_component_name_collisions(component_names)
    assert len(collisions) == 1
    assert "shared-comp" in collisions[0]


def test_no_collision_when_same_suite_excluded(virtual_ida_environment):
    suite = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0")])
    install_plugin_archive(suite, "my-suite")

    suite_v2 = _make_suite_zip("my-suite", "2.0.0", [("comp-a", "1.1.0")])
    path, meta = find_root_manifest_in_archive(suite_v2)
    component_names = collect_all_component_names_from_archive(suite_v2, path, meta)

    collisions = check_component_name_collisions(component_names, exclude_suite="my-suite")
    assert len(collisions) == 0


# ---------------------------------------------------------------------------
# find_suite_for_component
# ---------------------------------------------------------------------------


def test_find_suite_for_component_found(virtual_ida_environment):
    suite = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0")])
    install_plugin_archive(suite, "my-suite")

    record = find_suite_for_component("comp-a")
    assert record is not None
    assert record.name == "my-suite"


def test_find_suite_for_component_not_found(virtual_ida_environment):
    standalone = _make_standalone_zip("my-plugin", "1.0.0")
    install_plugin_archive(standalone, "my-plugin")

    record = find_suite_for_component("my-plugin")
    assert record is None


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------


def test_lint_suite_directory(virtual_ida_environment, tmp_path):
    suite_dir = tmp_path / "my-suite"
    suite_dir.mkdir()
    suite_meta = _make_plugin_metadata("my-suite", "1.0.0", components=["comp-a"])
    (suite_dir / "ida-plugin.json").write_text(json.dumps(suite_meta))
    (suite_dir / "my-suite.py").write_text("# suite")

    comp_dir = suite_dir / "comp-a"
    comp_dir.mkdir()
    comp_meta = _make_plugin_metadata("comp-a", "1.0.0")
    (comp_dir / "ida-plugin.json").write_text(json.dumps(comp_meta))
    (comp_dir / "comp-a.py").write_text("# component")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["lint", str(suite_dir)])
    assert result.exit_code == 0, result.output


def test_lint_suite_missing_component_dir(virtual_ida_environment, tmp_path):
    suite_dir = tmp_path / "my-suite"
    suite_dir.mkdir()
    suite_meta = _make_plugin_metadata("my-suite", "1.0.0", components=["comp-missing"])
    (suite_dir / "ida-plugin.json").write_text(json.dumps(suite_meta))
    (suite_dir / "my-suite.py").write_text("# suite")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["lint", str(suite_dir)])
    assert "comp-missing" in result.output
    assert "not found" in result.output.lower() or "error" in result.output.lower()
