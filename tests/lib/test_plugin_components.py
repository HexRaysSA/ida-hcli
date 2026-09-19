"""Tests for tightly-coupled plugin components (suites)."""

from __future__ import annotations

import io
import json
import logging
import zipfile

import pytest
from click.testing import CliRunner
from fixtures import *
from pydantic import ValidationError

from hcli.commands.plugin import plugin as plugin_group
from hcli.lib.ida.plugin import IDAMetadataDescriptor
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
    python_dependencies: list[str] | None = None,
    settings: list[dict] | None = None,
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
    if python_dependencies is not None:
        plugin["pythonDependencies"] = python_dependencies
    if settings is not None:
        plugin["settings"] = settings
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
    components: list[tuple[str, str] | tuple[str, str, dict]],
    *,
    suite_deps: list[str] | None = None,
    suite_python_deps: list[str] | None = None,
    suite_settings: list[dict] | None = None,
) -> bytes:
    buf = io.BytesIO()
    comp_names = [c[0] for c in components]
    suite_meta = _make_plugin_metadata(
        suite_name,
        suite_version,
        components=comp_names,
        deps=suite_deps,
        python_dependencies=suite_python_deps,
        settings=suite_settings,
    )

    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{suite_name}/ida-plugin.json", json.dumps(suite_meta))
        zf.writestr(f"{suite_name}/{suite_name}.py", "# suite entry point")

        for comp in components:
            comp_name, comp_version = comp[0], comp[1]
            comp_kwargs = comp[2] if len(comp) > 2 else {}
            comp_meta = _make_plugin_metadata(comp_name, comp_version, **comp_kwargs)
            zf.writestr(f"{suite_name}/{comp_name}/ida-plugin.json", json.dumps(comp_meta))
            zf.writestr(f"{suite_name}/{comp_name}/{comp_name}.py", "# component")

    return buf.getvalue()


def _make_nested_suite_zip(
    suite_name: str,
    suite_version: str,
    components: list[tuple[str, str, list[tuple[str, str]]]],
    *,
    suite_python_deps: list[str] | None = None,
    comp_python_deps: dict[str, list[str]] | None = None,
) -> bytes:
    """Create a suite with nested components (components that have their own components)."""
    buf = io.BytesIO()
    _comp_deps = comp_python_deps or {}
    top_comp_names = [name for name, _, _ in components]
    suite_meta = _make_plugin_metadata(
        suite_name,
        suite_version,
        components=top_comp_names,
        python_dependencies=suite_python_deps,
    )

    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{suite_name}/ida-plugin.json", json.dumps(suite_meta))
        zf.writestr(f"{suite_name}/{suite_name}.py", "# suite")

        for comp_name, comp_version, sub_components in components:
            sub_names = [n for n, _ in sub_components]
            comp_meta = _make_plugin_metadata(
                comp_name,
                comp_version,
                components=sub_names or None,
                python_dependencies=_comp_deps.get(comp_name),
            )
            zf.writestr(f"{suite_name}/{comp_name}/ida-plugin.json", json.dumps(comp_meta))
            zf.writestr(f"{suite_name}/{comp_name}/{comp_name}.py", "# component")

            for sub_name, sub_version in sub_components:
                sub_meta = _make_plugin_metadata(
                    sub_name,
                    sub_version,
                    python_dependencies=_comp_deps.get(sub_name),
                )
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
    _path, meta = find_root_manifest_in_archive(zip_data)
    assert meta.plugin.name == "my-plugin"


def test_find_root_manifest_in_suite():
    zip_data = _make_suite_zip("my-suite", "1.0.0", [("comp-a", "1.0.0"), ("comp-b", "2.0.0")])
    _path, meta = find_root_manifest_in_archive(zip_data)
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
        "my-suite",
        "1.0.0",
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


# ---------------------------------------------------------------------------
# Recursive Python dependency collection
# ---------------------------------------------------------------------------


def test_collect_python_deps_from_archive():
    from hcli.lib.ida.plugin.components import collect_python_dependencies_from_archive

    zip_data = _make_suite_zip(
        "my-suite",
        "1.0.0",
        [("comp-a", "1.0.0", {"python_dependencies": ["pyyaml"]})],
        suite_python_deps=["requests"],
    )
    path, meta = find_root_manifest_in_archive(zip_data)
    deps = collect_python_dependencies_from_archive(zip_data, path, meta)
    assert "requests" in deps
    assert "pyyaml" in deps


def test_collect_nested_python_deps_from_archive():
    from hcli.lib.ida.plugin.components import collect_python_dependencies_from_archive

    zip_data = _make_nested_suite_zip(
        "my-suite",
        "1.0.0",
        [("comp-a", "1.0.0", [("sub-x", "0.1.0")]), ("comp-b", "2.0.0", [])],
        suite_python_deps=["requests"],
        comp_python_deps={"comp-a": ["pyyaml"], "sub-x": ["toml"]},
    )
    path, meta = find_root_manifest_in_archive(zip_data)
    deps = collect_python_dependencies_from_archive(zip_data, path, meta)
    assert "requests" in deps
    assert "pyyaml" in deps
    assert "toml" in deps


def test_collect_python_deps_from_directory(virtual_ida_environment, tmp_path):
    from hcli.lib.ida.plugin.components import collect_python_dependencies_from_directory
    from hcli.lib.ida.plugin.install import get_metadata_from_plugin_directory

    suite_dir = tmp_path / "my-suite"
    suite_dir.mkdir()
    suite_meta = _make_plugin_metadata(
        "my-suite",
        "1.0.0",
        components=["comp-a"],
        python_dependencies=["requests"],
    )
    (suite_dir / "ida-plugin.json").write_text(json.dumps(suite_meta))
    (suite_dir / "my-suite.py").write_text("# suite")

    comp_dir = suite_dir / "comp-a"
    comp_dir.mkdir()
    comp_meta = _make_plugin_metadata("comp-a", "1.0.0", python_dependencies=["pyyaml"])
    (comp_dir / "ida-plugin.json").write_text(json.dumps(comp_meta))
    (comp_dir / "comp-a.py").write_text("# comp")

    metadata = get_metadata_from_plugin_directory(suite_dir)
    deps = collect_python_dependencies_from_directory(suite_dir, metadata)
    assert "requests" in deps
    assert "pyyaml" in deps


def test_collect_plugin_dependencies_includes_component_deps(virtual_ida_environment):
    from hcli.lib.ida.plugin.install import collect_plugin_dependencies, get_plugin_directory

    zip_data = _make_suite_zip(
        "my-suite",
        "1.0.0",
        [("comp-a", "1.0.0")],
    )
    install_plugin_archive(zip_data, "my-suite")

    suite_dir = get_plugin_directory("my-suite")
    suite_meta = _make_plugin_metadata(
        "my-suite",
        "1.0.0",
        components=["comp-a"],
        python_dependencies=["requests"],
    )
    (suite_dir / "ida-plugin.json").write_text(json.dumps(suite_meta))
    comp_meta = _make_plugin_metadata("comp-a", "1.0.0", python_dependencies=["pyyaml"])
    (suite_dir / "comp-a" / "ida-plugin.json").write_text(json.dumps(comp_meta))

    all_deps = collect_plugin_dependencies()
    suite_entry = next(d for d in all_deps if d.name == "my-suite")
    assert "requests" in suite_entry.dependencies
    assert "pyyaml" in suite_entry.dependencies


# ---------------------------------------------------------------------------
# Component settings via --config
# ---------------------------------------------------------------------------

SETTING_API_KEY = {
    "key": "api_key",
    "type": "string",
    "required": True,
    "name": "API Key",
    "prompt": True,
}

SETTING_VERBOSE = {
    "key": "verbose",
    "type": "boolean",
    "required": False,
    "default": False,
    "name": "Verbose",
    "prompt": True,
}


def test_install_config_with_component_prefix(virtual_ida_environment, tmp_path):
    zip_data = _make_suite_zip(
        "my-suite",
        "1.0.0",
        [("comp-a", "1.0.0", {"settings": [SETTING_API_KEY]})],
    )
    zip_path = tmp_path / "suite.zip"
    zip_path.write_bytes(zip_data)
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        plugin_group,
        ["install", str(zip_path), "--config", "comp-a.api_key=test-key-123"],
    )
    assert result.exit_code == 0, result.output

    from hcli.lib.ida import get_ida_config

    config = get_ida_config()
    assert "comp-a" in config.plugins
    assert config.plugins["comp-a"].settings["api_key"] == "test-key-123"


def test_install_config_without_prefix_targets_root(virtual_ida_environment, tmp_path):
    zip_data = _make_suite_zip(
        "my-suite",
        "1.0.0",
        [("comp-a", "1.0.0")],
        suite_settings=[SETTING_VERBOSE],
    )
    zip_path = tmp_path / "suite.zip"
    zip_path.write_bytes(zip_data)
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        plugin_group,
        ["install", str(zip_path), "--config", "verbose=true"],
    )
    assert result.exit_code == 0, result.output

    from hcli.lib.ida import get_ida_config

    config = get_ida_config()
    assert "my-suite" in config.plugins
    assert config.plugins["my-suite"].settings["verbose"] is True


def test_install_component_required_setting_nointeractive(virtual_ida_environment, tmp_path):
    from hcli.lib.console import console

    zip_data = _make_suite_zip(
        "my-suite",
        "1.0.0",
        [("comp-a", "1.0.0", {"settings": [SETTING_API_KEY]})],
    )
    zip_path = tmp_path / "suite.zip"
    zip_path.write_bytes(zip_data)
    runner = CliRunner(mix_stderr=False)
    old = console.is_interactive
    console.is_interactive = False
    try:
        result = runner.invoke(plugin_group, ["install", str(zip_path)])
    finally:
        console.is_interactive = old
    assert result.exit_code != 0
    assert "comp-a" in result.output
    assert "api_key" in result.output


# ---------------------------------------------------------------------------
# hcli plugin config for components
# ---------------------------------------------------------------------------


def test_config_list_for_component(virtual_ida_environment):
    zip_data = _make_suite_zip(
        "my-suite",
        "1.0.0",
        [("comp-a", "1.0.0", {"settings": [SETTING_API_KEY]})],
    )
    install_plugin_archive(zip_data, "my-suite", require_configuration=False)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["config", "comp-a", "list"])
    assert result.exit_code == 0, result.output
    assert "api_key" in result.output


def test_config_set_for_component(virtual_ida_environment):
    zip_data = _make_suite_zip(
        "my-suite",
        "1.0.0",
        [("comp-a", "1.0.0", {"settings": [SETTING_API_KEY]})],
    )
    install_plugin_archive(zip_data, "my-suite", require_configuration=False)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["config", "comp-a", "set", "api_key", "my-value"])
    assert result.exit_code == 0, result.output

    from hcli.lib.ida import get_ida_config

    config = get_ida_config()
    assert config.plugins["comp-a"].settings["api_key"] == "my-value"


def test_config_get_for_component(virtual_ida_environment):
    zip_data = _make_suite_zip(
        "my-suite",
        "1.0.0",
        [("comp-a", "1.0.0", {"settings": [SETTING_API_KEY]})],
    )
    install_plugin_archive(zip_data, "my-suite", require_configuration=False)

    from hcli.lib.ida.plugin.install import get_metadata_from_plugin_directory, get_plugin_directory
    from hcli.lib.ida.plugin.settings import set_setting_for_metadata

    comp_dir = get_plugin_directory("my-suite") / "comp-a"
    comp_meta = get_metadata_from_plugin_directory(comp_dir)
    set_setting_for_metadata("comp-a", "api_key", "test-val", comp_meta)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["config", "comp-a", "get", "api_key"])
    assert result.exit_code == 0, result.output
    assert "test-val" in result.output


# ---------------------------------------------------------------------------
# Suite install layout for component metadata
# ---------------------------------------------------------------------------


def test_suite_install_creates_component_metadata(virtual_ida_environment):
    zip_data = _make_suite_zip(
        "my-suite",
        "1.0.0",
        [("comp-a", "1.0.0")],
    )
    install_plugin_archive(zip_data, "my-suite")

    from hcli.lib.ida.plugin.install import get_metadata_from_plugin_directory, get_plugin_directory

    suite_dir = get_plugin_directory("my-suite")
    comp_file = suite_dir / "comp-a" / "comp-a.py"
    assert comp_file.exists()

    metadata = get_metadata_from_plugin_directory(suite_dir)
    assert metadata.plugin.name == "my-suite"

    comp_dir = suite_dir / "comp-a"
    comp_metadata = get_metadata_from_plugin_directory(comp_dir)
    assert comp_metadata.plugin.name == "comp-a"


def test_suite_install_creates_root_entry_point(virtual_ida_environment):
    zip_data = _make_suite_zip(
        "my-suite",
        "1.0.0",
        [("comp-a", "1.0.0")],
    )
    install_plugin_archive(zip_data, "my-suite")

    from hcli.lib.ida.plugin.install import get_metadata_from_plugin_directory, get_plugin_directory

    suite_dir = get_plugin_directory("my-suite")
    root_file = suite_dir / "my-suite.py"
    assert root_file.exists()

    metadata = get_metadata_from_plugin_directory(suite_dir)
    assert metadata.plugin.name == "my-suite"


# ---------------------------------------------------------------------------
# Depth-2+ nested component tests (grandchildren)
# ---------------------------------------------------------------------------


def test_nested_deps_depth2_collected_from_archive():
    """Root -> comp-a -> sub-x: all three levels' deps are collected."""
    from hcli.lib.ida.plugin.components import collect_python_dependencies_from_archive

    zip_data = _make_nested_suite_zip(
        "my-suite",
        "1.0.0",
        [("comp-a", "1.0.0", [("sub-x", "0.1.0")])],
        suite_python_deps=["requests"],
        comp_python_deps={"comp-a": ["pyyaml"], "sub-x": ["toml"]},
    )
    path, meta = find_root_manifest_in_archive(zip_data)
    deps = collect_python_dependencies_from_archive(zip_data, path, meta)
    assert "requests" in deps
    assert "pyyaml" in deps
    assert "toml" in deps


def test_nested_deps_depth2_collected_from_directory(virtual_ida_environment, tmp_path):
    """Build a depth-2 suite on disk and verify all component deps are collected."""
    from hcli.lib.ida.plugin.components import collect_python_dependencies_from_directory
    from hcli.lib.ida.plugin.install import get_metadata_from_plugin_directory

    suite_dir = tmp_path / "my-suite"
    suite_dir.mkdir()
    suite_meta = _make_plugin_metadata(
        "my-suite",
        "1.0.0",
        components=["comp-a"],
        python_dependencies=["requests"],
    )
    (suite_dir / "ida-plugin.json").write_text(json.dumps(suite_meta))
    (suite_dir / "my-suite.py").write_text("# suite")

    comp_dir = suite_dir / "comp-a"
    comp_dir.mkdir()
    comp_meta = _make_plugin_metadata(
        "comp-a",
        "1.0.0",
        components=["sub-x"],
        python_dependencies=["pyyaml"],
    )
    (comp_dir / "ida-plugin.json").write_text(json.dumps(comp_meta))
    (comp_dir / "comp-a.py").write_text("# comp")

    sub_dir = comp_dir / "sub-x"
    sub_dir.mkdir()
    sub_meta = _make_plugin_metadata("sub-x", "0.1.0", python_dependencies=["toml"])
    (sub_dir / "ida-plugin.json").write_text(json.dumps(sub_meta))
    (sub_dir / "sub-x.py").write_text("# sub")

    metadata = get_metadata_from_plugin_directory(suite_dir)
    deps = collect_python_dependencies_from_directory(suite_dir, metadata)
    assert "requests" in deps
    assert "pyyaml" in deps
    assert "toml" in deps


def test_nested_settings_depth2_via_config(virtual_ida_environment, tmp_path):
    """Install depth-2 suite passing --config for grandchild component settings."""
    setting_token = {
        "key": "token",
        "type": "string",
        "required": True,
        "name": "Token",
        "prompt": True,
    }

    buf = io.BytesIO()
    suite_meta = _make_plugin_metadata("my-suite", "1.0.0", components=["comp-a"])
    comp_meta = _make_plugin_metadata("comp-a", "1.0.0", components=["sub-x"])
    sub_meta = _make_plugin_metadata("sub-x", "0.1.0", settings=[setting_token])

    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("my-suite/ida-plugin.json", json.dumps(suite_meta))
        zf.writestr("my-suite/my-suite.py", "# suite")
        zf.writestr("my-suite/comp-a/ida-plugin.json", json.dumps(comp_meta))
        zf.writestr("my-suite/comp-a/comp-a.py", "# comp")
        zf.writestr("my-suite/comp-a/sub-x/ida-plugin.json", json.dumps(sub_meta))
        zf.writestr("my-suite/comp-a/sub-x/sub-x.py", "# sub")

    zip_path = tmp_path / "suite.zip"
    zip_path.write_bytes(buf.getvalue())
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        plugin_group,
        ["install", str(zip_path), "--config", "sub-x.token=secret-123"],
    )
    assert result.exit_code == 0, result.output

    from hcli.lib.ida import get_ida_config

    config = get_ida_config()
    assert "sub-x" in config.plugins
    assert config.plugins["sub-x"].settings["token"] == "secret-123"


def test_nested_component_walk_depth2(virtual_ida_environment):
    """Walk installed depth-2 suite and verify grandchild appears."""
    zip_data = _make_nested_suite_zip(
        "my-suite",
        "1.0.0",
        [("comp-a", "1.0.0", [("sub-x", "0.1.0")])],
    )
    install_plugin_archive(zip_data, "my-suite")

    from hcli.lib.ida.plugin.install import get_plugin_directory

    suite_dir = get_plugin_directory("my-suite")
    tree = walk_component_tree_from_directory(suite_dir)
    names = {m.plugin.name for _, m in tree}
    assert names == {"comp-a", "sub-x"}

    assert (suite_dir / "comp-a" / "sub-x" / "ida-plugin.json").exists()


# ---------------------------------------------------------------------------
# Undeclared component detection
# ---------------------------------------------------------------------------


def test_undeclared_plugin_detected_in_directory(virtual_ida_environment, tmp_path):
    """A subdirectory with ida-plugin.json not listed in components triggers a warning."""
    from hcli.lib.ida.plugin.components import find_undeclared_plugins_in_directory

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

    stray_dir = suite_dir / "sneaky-plugin"
    stray_dir.mkdir()
    stray_meta = _make_plugin_metadata("sneaky-plugin", "1.0.0")
    (stray_dir / "ida-plugin.json").write_text(json.dumps(stray_meta))
    (stray_dir / "sneaky-plugin.py").write_text("# sneaky")

    undeclared = find_undeclared_plugins_in_directory(suite_dir)
    assert len(undeclared) == 1
    assert undeclared[0][1].plugin.name == "sneaky-plugin"


def test_no_undeclared_when_all_referenced(virtual_ida_environment, tmp_path):
    """No undeclared plugins when every subdirectory plugin is declared."""
    from hcli.lib.ida.plugin.components import find_undeclared_plugins_in_directory

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

    undeclared = find_undeclared_plugins_in_directory(suite_dir)
    assert len(undeclared) == 0


def test_undeclared_plugin_detected_in_archive():
    """An archive with a manifest not in the component tree is flagged."""
    from pathlib import Path

    from hcli.lib.ida.plugin.components import find_undeclared_plugins_in_archive

    buf = io.BytesIO()
    suite_meta_dict = _make_plugin_metadata("my-suite", "1.0.0", components=["comp-a"])
    comp_meta = _make_plugin_metadata("comp-a", "1.0.0")
    stray_meta = _make_plugin_metadata("stray-plugin", "1.0.0")

    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("my-suite/ida-plugin.json", json.dumps(suite_meta_dict))
        zf.writestr("my-suite/my-suite.py", "# suite")
        zf.writestr("my-suite/comp-a/ida-plugin.json", json.dumps(comp_meta))
        zf.writestr("my-suite/comp-a/comp-a.py", "# comp")
        zf.writestr("my-suite/stray-plugin/ida-plugin.json", json.dumps(stray_meta))
        zf.writestr("my-suite/stray-plugin/stray-plugin.py", "# stray")

    zip_data = buf.getvalue()
    root_path = Path("my-suite/ida-plugin.json")
    root_meta = IDAMetadataDescriptor.model_validate(suite_meta_dict)
    undeclared = find_undeclared_plugins_in_archive(zip_data, root_path, root_meta)
    assert len(undeclared) == 1
    assert undeclared[0][1].plugin.name == "stray-plugin"


def test_lint_warns_undeclared_component(virtual_ida_environment, tmp_path):
    """Lint detects unreferenced plugin subdirectories."""
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

    stray_dir = suite_dir / "sneaky"
    stray_dir.mkdir()
    stray_meta = _make_plugin_metadata("sneaky", "1.0.0")
    (stray_dir / "ida-plugin.json").write_text(json.dumps(stray_meta))
    (stray_dir / "sneaky.py").write_text("# sneaky")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["lint", str(suite_dir)])
    assert "sneaky" in result.output
    assert "not declared" in result.output.lower() or "warning" in result.output.lower()


def test_undeclared_nested_grandchild(virtual_ida_environment, tmp_path):
    """A stray plugin under a declared component is also detected."""
    from hcli.lib.ida.plugin.components import find_undeclared_plugins_in_directory

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

    stray_dir = comp_dir / "hidden-plugin"
    stray_dir.mkdir()
    stray_meta = _make_plugin_metadata("hidden-plugin", "1.0.0")
    (stray_dir / "ida-plugin.json").write_text(json.dumps(stray_meta))
    (stray_dir / "hidden-plugin.py").write_text("# hidden")

    undeclared = find_undeclared_plugins_in_directory(suite_dir)
    assert len(undeclared) == 1
    assert undeclared[0][1].plugin.name == "hidden-plugin"
