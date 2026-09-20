"""Tests for component union entries and recursive metadata enrichment."""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

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


def _with_entry_points(files: dict[str, str | bytes]) -> dict[str, str | bytes]:
    """Add a stub entry point next to each parseable manifest that lacks one."""
    out: dict[str, str | bytes] = dict(files)
    for path, content in files.items():
        if not path.endswith("ida-plugin.json"):
            continue
        try:
            manifest = json.loads(content)
            entry_point = manifest["plugin"]["entryPoint"]
        except (ValueError, KeyError, TypeError):
            continue
        entry_path = (PurePosixPath(path).parent / entry_point).as_posix()
        out.setdefault(entry_path, "# plugin")
    return out


def _make_archive(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in _with_entry_points(files).items():
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
    with pytest.raises(ValueError, match=re.escape(str(Path("suite/comp-a/ida-plugin.json")))):
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
    assert [(p.as_posix(), m.plugin.name) for p, m in tree] == [("suite/comp-a/ida-plugin.json", "comp-a")]


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
    assert [p.as_posix() for p, _ in undeclared] == ["suite/stray/ida-plugin.json"]


# ---------------------------------------------------------------------------
# expand_metadata_from_archive
# ---------------------------------------------------------------------------

INLINE_ENTRY = """# /// script
# dependencies = ["{dep}"]
# ///
"""


def _inline_entry(dep: str) -> str:
    return INLINE_ENTRY.format(dep=dep)


def _make_three_level_archive() -> bytes:
    grandchild = _make_manifest("sub-x", "3.0.0", python_dependencies="inline", dependencies=["dep-c"])
    child = _make_manifest("comp-a", "2.0.0", components=["sub-x"], python_dependencies=[])
    root = _make_manifest("suite", "1.0.0", components=["comp-a"], python_dependencies="inline")
    return _make_archive(
        {
            "suite/ida-plugin.json": json.dumps(root),
            "suite/suite.py": _inline_entry("root-dep"),
            "suite/comp-a/ida-plugin.json": json.dumps(child),
            "suite/comp-a/comp-a.py": "# child",
            "suite/comp-a/sub-x/ida-plugin.json": json.dumps(grandchild),
            "suite/comp-a/sub-x/sub-x.py": _inline_entry("leaf-dep"),
        }
    )


def test_expand_archive_embeds_every_level_and_materializes_inline_deps():
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_archive

    archive = _make_three_level_archive()
    expanded = expand_metadata_from_archive(archive, Path("suite/ida-plugin.json"))

    assert expanded.plugin.python_dependencies == ["root-dep"]
    child = expanded.plugin.components[0]
    assert isinstance(child, IDAMetadataDescriptor)
    assert child.plugin.name == "comp-a"
    assert child.plugin.python_dependencies == []
    grandchild = child.plugin.components[0]
    assert isinstance(grandchild, IDAMetadataDescriptor)
    assert grandchild.plugin.name == "sub-x"
    assert grandchild.plugin.python_dependencies == ["leaf-dep"]
    assert grandchild.plugin.dependencies == [DependencySpec(plugin="dep-c")]
    assert is_expanded_for_planning(expanded)


def test_expand_archive_leaves_bytes_unchanged_and_is_pure():
    import hashlib

    from hcli.lib.ida.plugin.enrich import expand_metadata_from_archive

    archive = _make_three_level_archive()
    before = hashlib.sha256(archive).hexdigest()
    first = expand_metadata_from_archive(archive, Path("suite/ida-plugin.json"))
    second = expand_metadata_from_archive(archive, Path("suite/ida-plugin.json"))
    assert hashlib.sha256(archive).hexdigest() == before
    assert first.model_dump() == second.model_dump()

    raw = json.loads(zipfile.ZipFile(io.BytesIO(archive)).read("suite/ida-plugin.json"))
    assert raw["plugin"]["components"] == ["comp-a"]
    assert raw["plugin"]["pythonDependencies"] == "inline"


def test_expand_archive_round_trips_through_json():
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_archive

    expanded = expand_metadata_from_archive(_make_three_level_archive(), Path("suite/ida-plugin.json"))
    reparsed = IDAMetadataDescriptor.model_validate_json(expanded.model_dump_json())
    assert reparsed.model_dump() == expanded.model_dump()
    assert is_expanded_for_planning(reparsed)


def test_expand_archive_requires_exact_child_path():
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_archive

    root = _make_manifest("suite", components=["comp-a"])
    comp = _make_manifest("comp-a")
    archive = _make_archive(
        {
            "suite/ida-plugin.json": json.dumps(root),
            "suite/suite.py": "# root",
            "suite/nested/comp-a/ida-plugin.json": json.dumps(comp),
        }
    )
    with pytest.raises(ValueError, match=r"suite/comp-a/ida-plugin\.json"):
        expand_metadata_from_archive(archive, Path("suite/ida-plugin.json"))


def test_expand_archive_fails_on_missing_root():
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_archive

    archive = _make_archive({"other/ida-plugin.json": json.dumps(_make_manifest("other"))})
    with pytest.raises(ValueError, match="suite/ida-plugin"):
        expand_metadata_from_archive(archive, Path("suite/ida-plugin.json"))


def test_expand_archive_fails_on_malformed_child():
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_archive

    root = _make_manifest("suite", components=["comp-a"])
    archive = _make_archive(
        {
            "suite/ida-plugin.json": json.dumps(root),
            "suite/comp-a/ida-plugin.json": "{not json",
        }
    )
    with pytest.raises(ValueError, match="comp-a"):
        expand_metadata_from_archive(archive, Path("suite/ida-plugin.json"))


def test_expand_archive_fails_on_child_name_mismatch():
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_archive

    root = _make_manifest("suite", components=["comp-a"])
    archive = _make_archive(
        {
            "suite/ida-plugin.json": json.dumps(root),
            "suite/comp-a/ida-plugin.json": json.dumps(_make_manifest("other-name")),
        }
    )
    with pytest.raises(ValueError, match="mismatch"):
        expand_metadata_from_archive(archive, Path("suite/ida-plugin.json"))


def test_expand_archive_fails_when_inline_entry_point_missing():
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_archive

    root = _make_manifest("suite", python_dependencies="inline")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("suite/ida-plugin.json", json.dumps(root))
    with pytest.raises(ValueError, match=r"suite\.py"):
        expand_metadata_from_archive(buf.getvalue(), Path("suite/ida-plugin.json"))


def test_expand_archive_validates_embedded_source_entries_against_physical_child():
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_archive

    physical = _make_manifest("comp-a", "2.0.0", python_dependencies=["physical-dep"])
    matching_root = _make_manifest("suite", components=[_make_manifest("comp-a", "2.0.0")])
    archive = _make_archive(
        {
            "suite/ida-plugin.json": json.dumps(matching_root),
            "suite/comp-a/ida-plugin.json": json.dumps(physical),
        }
    )
    expanded = expand_metadata_from_archive(archive, Path("suite/ida-plugin.json"))
    child = expanded.plugin.components[0]
    assert isinstance(child, IDAMetadataDescriptor)
    assert child.plugin.python_dependencies == ["physical-dep"]

    stale_root = _make_manifest("suite", components=[_make_manifest("comp-a", "1.0.0")])
    stale = _make_archive(
        {
            "suite/ida-plugin.json": json.dumps(stale_root),
            "suite/comp-a/ida-plugin.json": json.dumps(physical),
        }
    )
    with pytest.raises(ValueError, match=r"1\.0\.0"):
        expand_metadata_from_archive(stale, Path("suite/ida-plugin.json"))


def test_expand_archive_is_depth_bounded():
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_archive

    files: dict[str, str | bytes] = {}
    prefix = "n0"
    for i in range(MAX_COMPONENT_DEPTH + 1):
        child = f"n{i + 1}"
        files[f"{prefix}/ida-plugin.json"] = json.dumps(_make_manifest(f"n{i}", components=[child]))
        prefix = f"{prefix}/{child}"
    files[f"{prefix}/ida-plugin.json"] = json.dumps(_make_manifest(f"n{MAX_COMPONENT_DEPTH + 1}"))
    with pytest.raises(ValueError, match="depth"):
        expand_metadata_from_archive(_make_archive(files), Path("n0/ida-plugin.json"))


# ---------------------------------------------------------------------------
# expand_metadata_from_directory
# ---------------------------------------------------------------------------


def _write_tree(base: Path, files: dict[str, str]) -> None:
    for rel, content in _with_entry_points(dict(files)).items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        assert isinstance(content, str)
        target.write_text(content, encoding="utf-8")


def test_expand_directory_embeds_components_and_materializes_inline_deps(tmp_path: Path):
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_directory

    _write_tree(
        tmp_path,
        {
            "suite/ida-plugin.json": json.dumps(
                _make_manifest("suite", components=["comp-a"], python_dependencies="inline")
            ),
            "suite/suite.py": _inline_entry("root-dep"),
            "suite/comp-a/ida-plugin.json": json.dumps(_make_manifest("comp-a", python_dependencies="inline")),
            "suite/comp-a/comp-a.py": _inline_entry("child-dep"),
        },
    )
    expanded = expand_metadata_from_directory(tmp_path / "suite")
    assert expanded.plugin.python_dependencies == ["root-dep"]
    child = expanded.plugin.components[0]
    assert isinstance(child, IDAMetadataDescriptor)
    assert child.plugin.python_dependencies == ["child-dep"]
    assert is_expanded_for_planning(expanded)


def test_expand_directory_fails_on_missing_component_dir(tmp_path: Path):
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_directory

    _write_tree(tmp_path, {"suite/ida-plugin.json": json.dumps(_make_manifest("suite", components=["comp-a"]))})
    with pytest.raises(ValueError, match="comp-a"):
        expand_metadata_from_directory(tmp_path / "suite")


def test_expand_directory_rejects_symlink_cycles(tmp_path: Path):
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_directory

    suite = tmp_path / "suite"
    _write_tree(tmp_path, {"suite/ida-plugin.json": json.dumps(_make_manifest("suite", components=["suite"]))})
    (suite / "suite").symlink_to(suite, target_is_directory=True)
    with pytest.raises(ValueError, match=r"cycle|depth"):
        expand_metadata_from_directory(suite)


# ---------------------------------------------------------------------------
# indexer and snapshot publication
# ---------------------------------------------------------------------------


def test_indexer_publishes_only_expanded_roots():
    from hcli.lib.ida.plugin.repo import PluginArchiveIndex

    index = PluginArchiveIndex()
    index.index_plugin_archive(_make_three_level_archive(), "file:///suite.zip")
    plugins = index.get_plugins()
    assert [p.name for p in plugins] == ["suite"]

    location = plugins[0].versions["1.0.0"][0]
    assert is_expanded_for_planning(location.metadata)
    names = [name for _, desc in iter_expanded_components(location.metadata) for name in [desc.plugin.name]]
    assert names == ["comp-a", "sub-x"]


def test_indexer_skips_root_that_fails_enrichment(caplog):
    from hcli.lib.ida.plugin.repo import PluginArchiveIndex

    broken = _make_archive(
        {
            "suite/ida-plugin.json": json.dumps(_make_manifest("suite", components=["comp-a"])),
            "suite/suite.py": "# root",
        }
    )
    index = PluginArchiveIndex()
    with caplog.at_level("WARNING"):
        index.index_plugin_archive(broken, "file:///broken.zip")
    assert index.get_plugins() == []
    assert any("comp-a" in record.getMessage() for record in caplog.records)


def test_indexer_keeps_variants_with_same_name_and_version_but_different_trees():
    from hcli.lib.ida.plugin.repo import PluginArchiveIndex

    def variant(component: str) -> bytes:
        return _make_archive(
            {
                "suite/ida-plugin.json": json.dumps(_make_manifest("suite", components=[component])),
                "suite/suite.py": "# root",
                f"suite/{component}/ida-plugin.json": json.dumps(_make_manifest(component)),
            }
        )

    index = PluginArchiveIndex()
    index.index_plugin_archive(variant("comp-a"), "file:///a.zip")
    index.index_plugin_archive(variant("comp-b"), "file:///b.zip")
    locations = index.get_plugins()[0].versions["1.0.0"]
    trees = {loc.url: list(iter_component_names(loc.metadata.plugin)) for loc in locations}
    assert trees == {"file:///a.zip": ["comp-a"], "file:///b.zip": ["comp-b"]}


def test_indexer_snapshot_round_trip_preserves_expanded_tree():
    from hcli.lib.ida.plugin.repo import PluginArchiveIndex
    from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo

    index = PluginArchiveIndex()
    index.index_plugin_archive(_make_three_level_archive(), "file:///suite.zip")
    repo = JSONFilePluginRepo(index.get_plugins())
    loaded = JSONFilePluginRepo.from_json(repo.to_json())
    original = index.get_plugins()[0].versions["1.0.0"][0].metadata
    restored = loaded.get_plugins()[0].versions["1.0.0"][0].metadata
    assert restored.model_dump() == original.model_dump()


def test_to_json_rejects_incomplete_metadata():
    from hcli.lib.ida.plugin.repo import Plugin, PluginArchiveLocation
    from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo

    incomplete = _descriptor(_make_manifest("suite", components=["comp-a"]))
    location = PluginArchiveLocation(url="file:///suite.zip", sha256="00" * 32, metadata=incomplete)
    repo = JSONFilePluginRepo([Plugin(name="suite", host=HOST, versions={"1.0.0": [location]})])
    with pytest.raises(ValueError, match="comp-a"):
        repo.to_json()
