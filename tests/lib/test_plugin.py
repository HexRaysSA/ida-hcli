import json

import pytest
from fixtures import PLUGINS_DIR

from hcli.lib.ida.plugin import (
    IDAMetadataDescriptor,
    PluginMetadata,
    URLs,
    is_binary_plugin_archive,
    is_ida_version_compatible,
    is_plugin_archive,
    is_source_plugin_archive,
)


def test_source_plugin_archive():
    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    assert is_plugin_archive(buf, "plugin1")
    assert is_source_plugin_archive(buf, "plugin1")
    assert not is_binary_plugin_archive(buf, "plugin1")


def test_binary_plugin_archive():
    plugin_path = PLUGINS_DIR / "zydisinfo" / "zydisinfo-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    assert is_plugin_archive(buf, "zydisinfo")
    assert not is_source_plugin_archive(buf, "zydisinfo")
    assert is_binary_plugin_archive(buf, "zydisinfo")


def test_is_ida_version_compatible():
    # Test exact version matches
    assert is_ida_version_compatible("9.0", ["9.0"])
    assert is_ida_version_compatible("9.0", ["9.0", "9.1"])
    assert is_ida_version_compatible("9.1", ["9.0", "9.1", "9.2"])
    assert is_ida_version_compatible("9.0sp1", ["9.0sp1"])
    assert is_ida_version_compatible("9.0sp1", ["9.0", "9.0sp1", "9.1"])

    # Test version not in list
    assert not is_ida_version_compatible("9.2", ["9.0", "9.1"])
    assert not is_ida_version_compatible("8.5", ["9.0", "9.1"])
    assert not is_ida_version_compatible("9.0sp1", ["9.0", "9.1"])  # sp1 not in list


def test_parse_plugin_version():
    metadata_path = PLUGINS_DIR / "plugin1" / "src-v1" / "ida-plugin.json"

    doc = json.loads(metadata_path.read_text())
    doc["plugin"]["version"] = "2025.09.24"
    with pytest.raises(ValueError):
        _ = IDAMetadataDescriptor.model_validate_json(json.dumps(doc))

    doc["plugin"]["version"] = "2025.9.24"
    _ = IDAMetadataDescriptor.model_validate_json(json.dumps(doc))


def test_parse_ida_versions():
    metadata_path = PLUGINS_DIR / "plugin1" / "src-v1" / "ida-plugin.json"

    doc = json.loads(metadata_path.read_text())
    doc["plugin"]["idaVersions"] = "==9.0.0"
    m = IDAMetadataDescriptor.model_validate_json(json.dumps(doc))
    assert m.plugin.ida_versions == ["9.0"]

    doc = json.loads(metadata_path.read_text())
    doc["plugin"]["idaVersions"] = "==9.0"
    m = IDAMetadataDescriptor.model_validate_json(json.dumps(doc))
    assert set(m.plugin.ida_versions) == {"9.0", "9.0sp1"}

    doc = json.loads(metadata_path.read_text())
    doc["plugin"]["idaVersions"] = ">=9.0"
    m = IDAMetadataDescriptor.model_validate_json(json.dumps(doc))
    assert "9.0" in m.plugin.ida_versions
    assert "9.1" in m.plugin.ida_versions
    assert "8.5" not in m.plugin.ida_versions

    doc = json.loads(metadata_path.read_text())
    doc["plugin"]["idaVersions"] = ">=9.0,<9.2"
    m = IDAMetadataDescriptor.model_validate_json(json.dumps(doc))
    assert "9.0" in m.plugin.ida_versions
    assert "9.1" in m.plugin.ida_versions
    assert "8.5" not in m.plugin.ida_versions
    assert "9.2" not in m.plugin.ida_versions


def test_unexpected_keys_in_plugin_metadata():
    metadata_path = PLUGINS_DIR / "plugin1" / "src-v1" / "ida-plugin.json"

    doc = json.loads(metadata_path.read_text())
    doc["plugin"]["unexpectedKey"] = "some value"
    doc["plugin"]["anotherBadKey"] = 123

    m = IDAMetadataDescriptor.model_validate_json(json.dumps(doc))

    assert hasattr(m.plugin, "__pydantic_extra__")
    assert m.plugin.__pydantic_extra__ is not None
    assert "unexpectedKey" in m.plugin.__pydantic_extra__
    assert "anotherBadKey" in m.plugin.__pydantic_extra__
    assert m.plugin.__pydantic_extra__["unexpectedKey"] == "some value"
    assert m.plugin.__pydantic_extra__["anotherBadKey"] == 123


def test_plugin_metadata_model_dump_uses_aliases():
    """Verify model_dump() returns JSON-aliased keys, not Python attribute names.

    Regression test for issue #128: search.py accesses metadata_dict["idaVersions"]
    which requires serialize_by_alias=True on PluginMetadata.
    """
    urls = URLs(repository="https://github.com/test/test")
    metadata = PluginMetadata(
        name="test",
        version="1.0.0",
        entryPoint="test.py",
        urls=urls,
        authors=[{"name": "Test Author", "email": "test@example.com"}],
    )
    dump = metadata.model_dump()

    # Keys must use JSON aliases (camelCase), not Python attribute names (snake_case)
    assert "idaVersions" in dump, "model_dump() must use alias 'idaVersions', not 'ida_versions'"
    assert "ida_versions" not in dump
    assert "entryPoint" in dump, "model_dump() must use alias 'entryPoint', not 'entry_point'"
    assert "entry_point" not in dump
    assert "logoPath" in dump, "model_dump() must use alias 'logoPath', not 'logo_path'"
    assert "logo_path" not in dump
