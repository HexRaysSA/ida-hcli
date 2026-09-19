"""Tests for component union entries and recursive metadata enrichment."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from hcli.lib.ida.plugin import (
    MAX_COMPONENT_DEPTH,
    DependencySpec,
    IDAMetadataDescriptor,
    get_component_name,
    get_ida_plugin_json_schema,
    is_expanded_for_planning,
    iter_component_names,
    iter_dependency_specs,
    iter_expanded_components,
    validate_expanded_for_planning,
)

HOST = "https://github.com/test/test-suite"


def _make_manifest(
    name: str,
    version: str = "1.0.0",
    *,
    components: list | None = None,
    dependencies: list | None = None,
    python_dependencies: list[str] | str | None = None,
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
    if dependencies is not None:
        plugin["dependencies"] = dependencies
    if python_dependencies is not None:
        plugin["pythonDependencies"] = python_dependencies
    return {"IDAMetadataDescriptorVersion": 1, "plugin": plugin}


def _descriptor(data: dict) -> IDAMetadataDescriptor:
    return IDAMetadataDescriptor.model_validate(data)


def _make_archive(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# components union field
# ---------------------------------------------------------------------------


def test_string_components_stay_strings():
    d = _descriptor(_make_manifest("suite", components=["comp-a", "comp-b"]))
    assert d.plugin.components == ["comp-a", "comp-b"]
    assert list(iter_component_names(d.plugin)) == ["comp-a", "comp-b"]


def test_object_components_become_descriptors():
    d = _descriptor(_make_manifest("suite", components=[_make_manifest("comp-a"), _make_manifest("comp-b", "2.0.0")]))
    assert all(isinstance(entry, IDAMetadataDescriptor) for entry in d.plugin.components)
    assert list(iter_component_names(d.plugin)) == ["comp-a", "comp-b"]
    assert d.plugin.components[1].plugin.version == "2.0.0"  # type: ignore[union-attr]


def test_mixed_components_preserve_order_and_names():
    d = _descriptor(_make_manifest("suite", components=["comp-a", _make_manifest("comp-b")]))
    assert [get_component_name(entry) for entry in d.plugin.components] == ["comp-a", "comp-b"]


def test_nested_object_components_parse_recursively():
    grandchild = _make_manifest("sub-x")
    child = _make_manifest("comp-a", components=[grandchild])
    d = _descriptor(_make_manifest("suite", components=[child]))
    expanded = list(iter_expanded_components(d))
    assert [(path, desc.plugin.name) for path, desc in expanded] == [
        (("comp-a",), "comp-a"),
        (("comp-a", "sub-x"), "sub-x"),
    ]


def test_malformed_object_component_is_rejected():
    bad_child = {"IDAMetadataDescriptorVersion": 1, "plugin": {"name": "comp-a"}}
    with pytest.raises(ValidationError):
        _descriptor(_make_manifest("suite", components=[bad_child]))


def test_component_entries_must_be_string_or_object():
    with pytest.raises(ValidationError):
        _descriptor(_make_manifest("suite", components=[42]))


def test_duplicate_component_names_across_forms_are_rejected():
    with pytest.raises(ValidationError, match="unique"):
        _descriptor(_make_manifest("suite", components=["comp-a", _make_manifest("comp-a")]))


@pytest.mark.parametrize("bad", ["comp-a==1.0.0", "comp-a@https://github.com/x/y", "bad name!", "-comp"])
def test_string_component_validation_still_applies(bad: str):
    with pytest.raises(ValidationError):
        _descriptor(_make_manifest("suite", components=[bad]))


def test_components_serialize_in_their_original_forms():
    d = _descriptor(_make_manifest("suite", components=["comp-a", _make_manifest("comp-b")]))
    dumped = d.model_dump(mode="json")["plugin"]["components"]
    assert dumped[0] == "comp-a"
    assert dumped[1]["IDAMetadataDescriptorVersion"] == 1
    assert dumped[1]["plugin"]["name"] == "comp-b"

    reparsed = IDAMetadataDescriptor.model_validate_json(d.model_dump_json())
    assert reparsed.model_dump() == d.model_dump()


def test_expanded_components_are_depth_bounded():
    leaf = _make_manifest("n0")
    node = leaf
    for i in range(1, MAX_COMPONENT_DEPTH + 1):
        node = _make_manifest(f"n{i}", components=[node])
    d = _descriptor(node)
    with pytest.raises(ValueError, match="depth"):
        list(iter_expanded_components(d))


def test_iter_dependency_specs_walks_every_embedded_node():
    grandchild = _make_manifest("sub-x", dependencies=["dep-c"])
    child = _make_manifest("comp-a", components=[grandchild], dependencies=[{"plugin": "dep-b", "required": False}])
    d = _descriptor(_make_manifest("suite", components=[child], dependencies=["dep-a"]))
    assert list(iter_dependency_specs(d)) == [
        ((), DependencySpec(plugin="dep-a")),
        (("comp-a",), DependencySpec(plugin="dep-b", required=False)),
        (("comp-a", "sub-x"), DependencySpec(plugin="dep-c")),
    ]


# ---------------------------------------------------------------------------
# publication completeness
# ---------------------------------------------------------------------------


def test_leaf_plugin_with_list_python_deps_is_expanded():
    assert is_expanded_for_planning(_descriptor(_make_manifest("leaf", python_dependencies=["requests"])))
    assert is_expanded_for_planning(_descriptor(_make_manifest("leaf")))


def test_inline_python_deps_are_not_expanded():
    d = _descriptor(_make_manifest("leaf", python_dependencies="inline"))
    assert not is_expanded_for_planning(d)
    with pytest.raises(ValueError, match="leaf"):
        validate_expanded_for_planning(d)


def test_string_component_is_not_expanded():
    d = _descriptor(_make_manifest("suite", components=["comp-a"]))
    assert not is_expanded_for_planning(d)
    with pytest.raises(ValueError, match="comp-a"):
        validate_expanded_for_planning(d)


def test_fully_embedded_tree_is_expanded():
    grandchild = _make_manifest("sub-x", python_dependencies=[])
    child = _make_manifest("comp-a", components=[grandchild])
    d = _descriptor(_make_manifest("suite", components=[child]))
    assert is_expanded_for_planning(d)
    validate_expanded_for_planning(d)


def test_deep_inline_node_is_reported_by_name():
    grandchild = _make_manifest("sub-x", python_dependencies="inline")
    child = _make_manifest("comp-a", components=[grandchild])
    d = _descriptor(_make_manifest("suite", components=[child]))
    with pytest.raises(ValueError, match="sub-x"):
        validate_expanded_for_planning(d)


def test_json_schema_components_accept_string_or_descriptor():
    schema = get_ida_plugin_json_schema()
    components = schema["$defs"]["PluginMetadata"]["properties"]["components"]["items"]
    kinds = {option.get("type") or option.get("$ref") for option in components["anyOf"]}
    assert "string" in kinds
    assert "#/$defs/IDAMetadataDescriptor" in kinds
    assert schema["title"] == "ida-plugin.json"
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$ref"] == "#/$defs/IDAMetadataDescriptor"
    assert "IDAMetadataDescriptorVersion" in schema["$defs"]["IDAMetadataDescriptor"]["properties"]


# ---------------------------------------------------------------------------
# archive walker path enforcement
# ---------------------------------------------------------------------------


def test_archive_walker_requires_component_at_exact_child_path():
    from hcli.lib.ida.plugin.components import walk_component_tree_from_archive

    suite = _make_manifest("suite", components=["comp-a"])
    comp = _make_manifest("comp-a")
    misplaced = _make_archive(
        {
            "suite/ida-plugin.json": json.dumps(suite),
            "suite/suite.py": "# suite",
            "elsewhere/comp-a/ida-plugin.json": json.dumps(comp),
            "elsewhere/comp-a/comp-a.py": "# component",
        }
    )
    with pytest.raises(ValueError, match=r"suite/comp-a/ida-plugin\.json"):
        walk_component_tree_from_archive(misplaced, Path("suite/ida-plugin.json"), _descriptor(suite))

    placed = _make_archive(
        {
            "suite/ida-plugin.json": json.dumps(suite),
            "suite/suite.py": "# suite",
            "suite/comp-a/ida-plugin.json": json.dumps(comp),
            "suite/comp-a/comp-a.py": "# component",
        }
    )
    tree = walk_component_tree_from_archive(placed, Path("suite/ida-plugin.json"), _descriptor(suite))
    assert [(str(p), m.plugin.name) for p, m in tree] == [("suite/comp-a/ida-plugin.json", "comp-a")]


def test_undeclared_detection_uses_paths_not_names():
    from hcli.lib.ida.plugin.components import find_undeclared_plugins_in_archive

    suite = _make_manifest("suite", components=["comp-a"])
    comp = _make_manifest("comp-a")
    archive = _make_archive(
        {
            "suite/ida-plugin.json": json.dumps(suite),
            "suite/comp-a/ida-plugin.json": json.dumps(comp),
            "suite/stray/ida-plugin.json": json.dumps(comp),
        }
    )
    undeclared = find_undeclared_plugins_in_archive(archive, Path("suite/ida-plugin.json"), _descriptor(suite))
    assert [str(p) for p, _ in undeclared] == ["suite/stray/ida-plugin.json"]
