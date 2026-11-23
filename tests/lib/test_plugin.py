import json

import pytest
from fixtures import PLUGINS_DIR
from pydantic import ValidationError

from hcli.commands.plugin.lint import LintResult, _lint_ida_versions
from hcli.lib.ida.plugin import (
    IDAMetadataDescriptor,
    PluginMetadata,
    PluginSettingDescriptor,
    URLs,
    get_ida_version_spec_from_list,
    is_binary_plugin_archive,
    is_ida_version_compatible,
    is_plugin_archive,
    is_python_version_compatible,
    is_source_plugin_archive,
    parse_plugin_version,
)
from hcli.lib.ida.plugin.exceptions import IDAVersionIncompatibleError


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


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("1.2.3", "1.2.3"),
        ("1.2", "1.2.0"),
        ("1", "1.0.0"),
        ("2025.09.24", "2025.9.24"),
    ],
)
def test_parse_plugin_version_normalizes(raw: str, normalized: str):
    assert str(parse_plugin_version(raw)) == normalized


def test_requires_python_metadata():
    metadata_path = PLUGINS_DIR / "plugin1" / "src-v1" / "ida-plugin.json"
    doc = json.loads(metadata_path.read_text())
    doc["plugin"]["requiresPython"] = ">=3.11,<3.14"

    metadata = IDAMetadataDescriptor.model_validate_json(json.dumps(doc))

    assert metadata.plugin.requires_python == ">=3.11,<3.14"
    assert metadata.plugin.model_dump()["requiresPython"] == ">=3.11,<3.14"


@pytest.mark.parametrize("requirement", ["", "3.11", ">=not-a-version"])
def test_requires_python_rejects_invalid_specifier(requirement):
    metadata_path = PLUGINS_DIR / "plugin1" / "src-v1" / "ida-plugin.json"
    doc = json.loads(metadata_path.read_text())
    doc["plugin"]["requiresPython"] = requirement

    with pytest.raises(ValueError, match="requiresPython"):
        IDAMetadataDescriptor.model_validate_json(json.dumps(doc))


def test_is_python_version_compatible():
    assert is_python_version_compatible("3.11.0", ">=3.11")
    assert is_python_version_compatible("3.13.7", ">=3.11,<3.14")
    assert not is_python_version_compatible("3.10.14", ">=3.11")
    assert not is_python_version_compatible("3.14.0", ">=3.11,<3.14")


def test_parse_plugin_version_sortable():
    """Regression test for commit 414a557.

    Before the fix, partial versions (like "1.0") had None components,
    which raised TypeError when sorted.
    """
    versions = ["1.0", "2.0.0", "1.5", "1.0.1", "3"]
    parsed = [parse_plugin_version(v) for v in versions]

    assert [str(v) for v in sorted(parsed)] == ["1.0.0", "1.0.1", "1.5.0", "2.0.0", "3.0.0"]


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


def _get_metadata_with_ida_versions(ida_versions: object = None) -> IDAMetadataDescriptor:
    doc = json.loads((PLUGINS_DIR / "plugin1" / "src-v1" / "ida-plugin.json").read_text())
    doc["plugin"].pop("idaVersions", None)
    if ida_versions is not None:
        doc["plugin"]["idaVersions"] = ida_versions
    return IDAMetadataDescriptor.model_validate_json(json.dumps(doc))


@pytest.mark.parametrize(
    ("versions", "spec"),
    [
        (["9.0", "9.1", "9.2"], ">=9.0,<=9.2"),
        (["9.0", "9.0sp1", "9.1", "9.2"], ">=9.0,<=9.2"),
        (["8.4", "9.0", "9.1", "9.2", "9.3"], ">=8.4,<=9.3,!=8.5"),
        (["9.5"], "==9.5"),
        (["9.0sp1"], "==9.0"),
        (["9.4", "9.6"], ">=9.4,<=9.6,!=9.5"),
        (["9.2", "9.0", "9.1"], ">=9.0,<=9.2"),
        ([], "*"),
    ],
)
def test_get_ida_version_spec_from_list(versions, spec):
    assert get_ida_version_spec_from_list(versions) == spec


def test_ida_version_list_matches_the_same_released_versions_as_its_spec():
    spec = _get_metadata_with_ida_versions(["8.4", "9.0", "9.1", "9.2", "9.3"]).plugin.ida_version_spec
    for version in ["8.4", "8.4sp2", "9.0", "9.0sp1", "9.3"]:
        assert is_ida_version_compatible(version, spec)
    for version in ["8.3", "8.5", "9.4"]:
        assert not is_ida_version_compatible(version, spec)


@pytest.mark.parametrize("ida_versions", [[">=9.4"], [9.0], 9.0, "9.0 - 9.2x"])
def test_invalid_ida_versions_are_rejected(ida_versions: object):
    with pytest.raises(ValidationError):
        _ = _get_metadata_with_ida_versions(ida_versions)


@pytest.mark.parametrize(
    ("ida_versions", "spec"),
    [
        (">=9.0,<10", ">=9.0,<10"),
        (">=9.0, <10", ">=9.0,<10"),
        (" >= 9.0 ", ">=9.0"),
    ],
)
def test_ida_version_spec_is_stored_without_whitespace(ida_versions: str, spec: str):
    m = _get_metadata_with_ida_versions(ida_versions)
    assert m.plugin.ida_version_spec == spec
    assert m.plugin.model_dump(mode="json")["idaVersions"] == spec


def test_omitted_ida_versions_matches_all_versions():
    m = _get_metadata_with_ida_versions()
    assert m.plugin.ida_version_spec == "*"
    assert m.plugin.model_dump(mode="json")["idaVersions"] == "*"
    assert is_ida_version_compatible("12.3", m.plugin.ida_version_spec)


def test_open_ida_version_spec_matches_unknown_releases():
    assert is_ida_version_compatible("9.6", ">=9.0")
    assert is_ida_version_compatible("10.0", ">=9.0")
    assert not is_ida_version_compatible("10.0", ">=9.0,<10")
    assert not is_ida_version_compatible("9.3", ">=9.0,!=9.3")


def test_unparseable_ida_version_matches_nothing():
    assert not is_ida_version_compatible("not-a-version", "*")


def test_ida_version_incompatible_error_accepts_a_list_without_a_spec():
    # ida-hcli-plugin creates the error with only the current version and a list.
    error = IDAVersionIncompatibleError("9.2", ["9.0", "9.1"])
    assert error.supported == ["9.0", "9.1"]
    assert error.spec == ">=9.0,<=9.1"
    assert ">=9.0,<=9.1" in str(error)


@pytest.mark.parametrize(
    ("ida_versions", "recommendations", "expected"),
    [
        (">=9.0", 0, None),
        (None, 1, "provide plugin.idaVersions"),
        (["9.0", "9.1"], 1, '"idaVersions": ">=9.0,<=9.1"'),
        (">=90", 1, "matches no IDA release"),
        ([], 1, "provide plugin.idaVersions"),
        (">=9.0sp1", 1, '"idaVersions": ">=9.0"'),
    ],
)
def test_lint_ida_versions(capsys, ida_versions, recommendations, expected):
    result = LintResult()
    _lint_ida_versions(_get_metadata_with_ida_versions(ida_versions), "test", result)
    assert result.recommendations == recommendations
    if expected is not None:
        assert expected in " ".join(capsys.readouterr().out.split())


def test_unexpected_keys_in_plugin_metadata():
    """Unknown keys must not fail validation, so an older hcli can read a newer ida-plugin.json."""
    metadata_path = PLUGINS_DIR / "plugin1" / "src-v1" / "ida-plugin.json"

    doc = json.loads(metadata_path.read_text())
    doc["plugin"]["unexpectedKey"] = "some value"
    doc["plugin"]["anotherBadKey"] = 123

    m = IDAMetadataDescriptor.model_validate_json(json.dumps(doc))

    dump = m.plugin.model_dump()
    assert dump["unexpectedKey"] == "some value"
    assert dump["anotherBadKey"] == 123


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
    assert "requiresPython" in dump, "model_dump() must use alias 'requiresPython', not 'requires_python'"
    assert "requires_python" not in dump


@pytest.mark.parametrize("required", [True, False])
def test_setting_prompt_false_requires_default(required: bool):
    with pytest.raises(ValueError, match="prompt=False requires a default value"):
        PluginSettingDescriptor(
            key="test",
            type="string",
            required=required,
            name="Test Setting",
            prompt=False,
        )

    setting = PluginSettingDescriptor(
        key="test",
        type="string",
        required=required,
        default="default-value",
        name="Test Setting",
        prompt=False,
    )
    assert setting.prompt is False
    assert setting.default == "default-value"


def test_setting_secret_not_allowed_for_boolean():
    with pytest.raises(ValueError, match="secret is only supported for string settings"):
        PluginSettingDescriptor(
            key="test",
            type="boolean",
            required=False,
            default=False,
            name="Test Setting",
            secret=True,
        )


def test_plugin_with_prompt_false_setting():
    metadata_path = PLUGINS_DIR / "plugin1" / "src-v5" / "ida-plugin.json"
    m = IDAMetadataDescriptor.model_validate_json(metadata_path.read_text())

    key5 = m.plugin.get_setting("key5")
    assert key5.prompt is False
    assert key5.default == "hidden-default"

    key6 = m.plugin.get_setting("key6")
    assert key6.secret is True
