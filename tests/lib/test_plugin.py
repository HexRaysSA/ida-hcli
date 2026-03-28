import json

import pytest
from fixtures import PLUGINS_DIR

from hcli.lib.ida.plugin import (
    IDAMetadataDescriptor,
    PluginMetadata,
    PluginSettingDescriptor,
    URLs,
    is_binary_plugin_archive,
    is_ida_version_compatible,
    is_plugin_archive,
    is_source_plugin_archive,
    parse_plugin_version,
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
    """Test version parsing with leading zeros - they should be normalized."""
    metadata_path = PLUGINS_DIR / "plugin1" / "src-v1" / "ida-plugin.json"

    # Versions with leading zeros are accepted and normalized
    doc = json.loads(metadata_path.read_text())
    doc["plugin"]["version"] = "2025.09.24"
    m = IDAMetadataDescriptor.model_validate_json(json.dumps(doc))
    assert m.plugin.version == "2025.09.24"  # The raw string is preserved in metadata

    # Test that the normalized version is used when parsing
    v = parse_plugin_version("2025.09.24")
    assert str(v) == "2025.9.24"  # Leading zeros are normalized

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


def test_setting_prompt_field_defaults_to_true():
    setting = PluginSettingDescriptor(
        key="test",
        type="string",
        required=True,
        name="Test Setting",
    )
    assert setting.prompt is True


def test_setting_prompt_false_requires_default_optional():
    with pytest.raises(ValueError, match="prompt=False requires a default value"):
        PluginSettingDescriptor(
            key="test",
            type="string",
            required=False,
            name="Test Setting",
            prompt=False,
        )


def test_setting_prompt_false_requires_default_required():
    with pytest.raises(ValueError, match="prompt=False requires a default value"):
        PluginSettingDescriptor(
            key="test",
            type="string",
            required=True,
            name="Test Setting",
            prompt=False,
        )


def test_setting_required_with_default_can_skip_prompt():
    setting = PluginSettingDescriptor(
        key="test",
        type="string",
        required=True,
        default="default-value",
        name="Test Setting",
        prompt=False,
    )
    assert setting.prompt is False
    assert setting.required is True
    assert setting.default == "default-value"


def test_setting_prompt_false_with_default():
    setting = PluginSettingDescriptor(
        key="test",
        type="string",
        required=False,
        default="default-value",
        name="Test Setting",
        prompt=False,
    )
    assert setting.prompt is False
    assert setting.default == "default-value"


def test_plugin_with_prompt_false_setting():
    metadata_path = PLUGINS_DIR / "plugin1" / "src-v5" / "ida-plugin.json"
    m = IDAMetadataDescriptor.model_validate_json(metadata_path.read_text())

    key5 = m.plugin.get_setting("key5")
    assert key5.prompt is False
    assert key5.default == "hidden-default"


def test_parse_plugin_version_returns_full_version():
    """Test that parse_plugin_version normalizes partial versions to full versions."""
    # Full version stays the same
    v = parse_plugin_version("1.2.3")
    assert str(v) == "1.2.3"

    # Two-component version gets patch=0
    v = parse_plugin_version("1.2")
    assert str(v) == "1.2.0"

    # Single-component version gets minor=0 and patch=0
    v = parse_plugin_version("1")
    assert str(v) == "1.0.0"


def test_parse_plugin_version_sortable():
    """Test that parsed versions can be sorted.

    This is a regression test for the fix in commit 414a557.
    Before the fix, partial versions (like "1.0") would have None components
    which caused TypeError when sorting because None cannot be compared to int.
    """
    versions = ["1.0", "2.0.0", "1.5", "1.0.1", "3"]
    parsed = [parse_plugin_version(v) for v in versions]

    # This would raise TypeError before the fix:
    # TypeError: '<' not supported between instances of 'NoneType' and 'int'
    sorted_versions = sorted(parsed)

    # Verify the sort order is correct
    assert str(sorted_versions[0]) == "1.0.0"
    assert str(sorted_versions[1]) == "1.0.1"
    assert str(sorted_versions[2]) == "1.5.0"
    assert str(sorted_versions[3]) == "2.0.0"
    assert str(sorted_versions[4]) == "3.0.0"


def test_parse_plugin_version_comparison():
    """Test that parsed versions can be compared."""
    v1 = parse_plugin_version("1.0")
    v2 = parse_plugin_version("1.0.1")
    v3 = parse_plugin_version("2")

    # These comparisons would fail before the fix with partial versions
    assert v1 < v2
    assert v2 < v3
    assert v1 < v3
    assert v3 > v1
    assert v2 > v1
