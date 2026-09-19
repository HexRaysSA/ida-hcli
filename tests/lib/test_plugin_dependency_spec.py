"""Tests for the DependencySpec model behind plugin.dependencies."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from hcli.lib.ida.plugin import DependencySpec, IDAMetadataDescriptor, PluginMetadata
from hcli.lib.ida.plugin.repo import Plugin, PluginArchiveLocation
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo

HOST = "https://github.com/test/test-pack"


def _make_descriptor(dependencies: object = None, **extra: object) -> dict:
    plugin: dict = {
        "name": "root",
        "version": "1.0.0",
        "entryPoint": "root.py",
        "urls": {"repository": HOST},
        "authors": [{"name": "Test", "email": "test@example.com"}],
    }
    if dependencies is not None:
        plugin["dependencies"] = dependencies
    plugin.update(extra)
    return {"IDAMetadataDescriptorVersion": 1, "plugin": plugin}


def _dependencies(data: dict) -> list[DependencySpec]:
    return IDAMetadataDescriptor.model_validate(data).plugin.dependencies


def test_legacy_string_entries_become_required_specs():
    specs = _dependencies(_make_descriptor(["dep-a", "dep-b==1.0.0", f"dep-c@{HOST}"]))
    assert specs == [
        DependencySpec(plugin="dep-a"),
        DependencySpec(plugin="dep-b==1.0.0"),
        DependencySpec(plugin=f"dep-c@{HOST}"),
    ]
    assert all(spec.required for spec in specs)


def test_object_entries_carry_required_flag():
    specs = _dependencies(
        _make_descriptor(
            [
                {"plugin": "dep-a"},
                {"plugin": "dep-b==2.0.0", "required": False},
                {"plugin": "dep-c", "required": True},
            ]
        )
    )
    assert [(s.plugin, s.required) for s in specs] == [
        ("dep-a", True),
        ("dep-b==2.0.0", False),
        ("dep-c", True),
    ]


def test_mixed_string_and_object_entries_preserve_order():
    specs = _dependencies(_make_descriptor(["dep-a", {"plugin": "dep-b", "required": False}, "dep-c==3.0.0"]))
    assert [s.plugin for s in specs] == ["dep-a", "dep-b", "dep-c==3.0.0"]
    assert [s.required for s in specs] == [True, False, True]


def test_omitted_dependencies_is_empty_list():
    assert _dependencies(_make_descriptor()) == []


def test_explicit_empty_dependencies_is_empty_list():
    assert _dependencies(_make_descriptor([])) == []


@pytest.mark.parametrize(
    "bad_entry",
    [
        {},
        {"plugin": ""},
        {"plugin": "!!!invalid"},
        {"plugin": "dep-a=="},
        {"plugin": "dep-a== "},
        {"plugin": "dep-a==not-a-version"},
        {"plugin": "dep-a>=1.0.0"},
        {"plugin": "repo/dep-a"},
        {"plugin": "dep-a", "required": "yes"},
        {"plugin": "dep-a", "required": 1},
        {"plugin": "dep-a", "optional": True},
        {"plugin": "dep-a", "version": "1.0.0"},
        "",
        "dep-a==",
        "dep-a>=1.0.0",
        "repo/dep-a",
        42,
        None,
        ["dep-a"],
    ],
)
def test_invalid_dependency_entries_are_rejected(bad_entry: object):
    with pytest.raises(ValidationError):
        IDAMetadataDescriptor.model_validate(_make_descriptor([bad_entry]))


def test_dependencies_must_be_a_list():
    with pytest.raises(ValidationError):
        IDAMetadataDescriptor.model_validate(_make_descriptor("dep-a"))


def test_dependency_spec_is_frozen():
    spec = DependencySpec(plugin="dep-a")
    with pytest.raises(ValidationError):
        spec.plugin = "dep-b"  # type: ignore[misc]


def test_dependency_spec_exposes_parsed_reference():
    spec = DependencySpec(plugin=f"dep-a==1.2.3@{HOST}")
    ref = spec.reference
    assert ref.name == "dep-a"
    assert ref.version_spec == "==1.2.3"
    assert ref.host == HOST
    assert spec.name == "dep-a"


def test_required_specs_serialize_as_strings_and_optional_as_objects():
    descriptor = IDAMetadataDescriptor.model_validate(
        _make_descriptor(["dep-a", {"plugin": "dep-b==1.0.0", "required": False}, {"plugin": "dep-c"}])
    )
    dumped = descriptor.model_dump(mode="json")
    assert dumped["plugin"]["dependencies"] == [
        "dep-a",
        {"plugin": "dep-b==1.0.0", "required": False},
        "dep-c",
    ]

    dumped_json = json.loads(descriptor.model_dump_json())
    assert dumped_json["plugin"]["dependencies"] == dumped["plugin"]["dependencies"]


def test_model_round_trip_preserves_specs():
    original = IDAMetadataDescriptor.model_validate(_make_descriptor(["dep-a", {"plugin": "dep-b", "required": False}]))
    reparsed = IDAMetadataDescriptor.model_validate_json(original.model_dump_json())
    assert reparsed.plugin.dependencies == original.plugin.dependencies
    assert reparsed.model_dump() == original.model_dump()


def test_plugin_metadata_accepts_dependency_spec_instances():
    metadata = PluginMetadata.model_validate(
        {
            "name": "root",
            "version": "1.0.0",
            "entryPoint": "root.py",
            "urls": {"repository": HOST},
            "authors": [{"name": "Test", "email": "test@example.com"}],
            "dependencies": [DependencySpec(plugin="dep-a", required=False)],
        }
    )
    assert metadata.dependencies == [DependencySpec(plugin="dep-a", required=False)]


def test_repository_snapshot_round_trip_preserves_optional_flag():
    descriptor = IDAMetadataDescriptor.model_validate(
        _make_descriptor(["dep-a", {"plugin": "dep-b==1.0.0", "required": False}])
    )
    location = PluginArchiveLocation(url="https://example.com/root.zip", sha256="00" * 32, metadata=descriptor)
    repo = JSONFilePluginRepo([Plugin(name="root", host=HOST, versions={"1.0.0": [location]})])

    doc = repo.to_json()
    encoded = json.loads(doc)["plugins"][0]["versions"]["1.0.0"][0]["metadata"]["plugin"]["dependencies"]
    assert encoded == ["dep-a", {"plugin": "dep-b==1.0.0", "required": False}]

    loaded = JSONFilePluginRepo.from_json(doc)
    loaded_specs = loaded.get_plugins()[0].versions["1.0.0"][0].metadata.plugin.dependencies
    assert loaded_specs == descriptor.plugin.dependencies


def test_json_schema_accepts_string_or_object_entries():
    schema = IDAMetadataDescriptor.model_json_schema(by_alias=True)
    dependencies = schema["$defs"]["PluginMetadata"]["properties"]["dependencies"]
    entry = dependencies["items"]
    assert "anyOf" in entry
    kinds = {option.get("type") or option.get("$ref") for option in entry["anyOf"]}
    assert "string" in kinds
    assert "#/$defs/DependencySpec" in kinds
    spec_schema = schema["$defs"]["DependencySpec"]
    assert spec_schema["additionalProperties"] is False
    assert spec_schema["required"] == ["plugin"]
    assert spec_schema["properties"]["required"]["default"] is True
