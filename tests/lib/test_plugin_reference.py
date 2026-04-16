import pytest

from hcli.lib.ida.plugin.reference import (
    PluginReference,
    format_qualified_plugin_reference,
    is_github_repository_url,
    normalize_plugin_host,
    parse_plugin_reference,
)


def test_is_github_repository_url_accepts_valid_repos():
    assert is_github_repository_url("https://github.com/org/repo")
    assert is_github_repository_url("https://github.com/org/repo/")
    assert is_github_repository_url("https://github.com/Hex-Rays/ida-hcli")


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/org/repo/blob/main",
        "not a url",
        "github.com/org/repo",
        "http://github.com/org/repo",
        "foo@https://github.com/org/repo",
        "prefix https://github.com/org/repo",
    ],
)
def test_is_github_repository_url_rejects_invalid_shapes(value: str):
    assert not is_github_repository_url(value)


def test_normalize_plugin_host_lowercases_components():
    assert normalize_plugin_host("HTTPS://GitHub.Com/Org/Repo") == "https://github.com/org/repo"


def test_normalize_plugin_host_strips_trailing_slash():
    assert normalize_plugin_host("https://github.com/org/repo/") == "https://github.com/org/repo"


def test_normalize_plugin_host_preserves_no_trailing_slash():
    assert normalize_plugin_host("https://github.com/org/repo") == "https://github.com/org/repo"


@pytest.mark.parametrize("value", ["not a url", ""])
def test_normalize_plugin_host_rejects_invalid_values(value: str):
    with pytest.raises(ValueError):
        normalize_plugin_host(value)


def test_parse_plugin_reference_bare_name():
    ref = parse_plugin_reference("plugin1")
    assert ref == PluginReference(name="plugin1", version_spec="", host=None)


def test_parse_plugin_reference_bare_version():
    ref = parse_plugin_reference("plugin1==1.0.0")
    assert ref == PluginReference(name="plugin1", version_spec="==1.0.0", host=None)


@pytest.mark.parametrize("op", ["==", ">=", "<=", "!=", "~="])
def test_parse_plugin_reference_various_operators(op: str):
    ref = parse_plugin_reference(f"plugin1{op}1.0.0")
    assert ref.name == "plugin1"
    assert ref.version_spec == f"{op}1.0.0"


def test_parse_plugin_reference_qualified_name():
    ref = parse_plugin_reference("plugin1@https://github.com/org/repo")
    assert ref == PluginReference(
        name="plugin1",
        version_spec="",
        host="https://github.com/org/repo",
    )


def test_parse_plugin_reference_qualified_version():
    ref = parse_plugin_reference("plugin1==1.0.0@https://github.com/org/repo")
    assert ref == PluginReference(
        name="plugin1",
        version_spec="==1.0.0",
        host="https://github.com/org/repo",
    )


def test_parse_plugin_reference_qualified_trailing_slash_is_normalized():
    ref = parse_plugin_reference("plugin1@https://github.com/org/repo/")
    assert ref.host == "https://github.com/org/repo"


def test_parse_plugin_reference_qualified_case_insensitive():
    ref = parse_plugin_reference("plugin1@https://GitHub.com/Org/Repo")
    assert ref.host == "https://github.com/org/repo"


def test_parse_plugin_reference_raw_github_url_is_not_a_plugin_reference():
    with pytest.raises(ValueError):
        parse_plugin_reference("https://github.com/org/repo")


@pytest.mark.parametrize("value", ["", "plugin1=1.0.0"])
def test_parse_plugin_reference_rejects_invalid_values(value: str):
    with pytest.raises(ValueError):
        parse_plugin_reference(value)


def test_format_qualified_plugin_reference_name_only():
    ref = PluginReference(name="plugin1", version_spec="", host="https://github.com/org/repo")
    assert format_qualified_plugin_reference(ref) == "plugin1@https://github.com/org/repo"


def test_format_qualified_plugin_reference_with_version():
    ref = PluginReference(name="plugin1", version_spec="==1.0.0", host="https://github.com/org/repo")
    assert format_qualified_plugin_reference(ref) == "plugin1==1.0.0@https://github.com/org/repo"


def test_format_qualified_plugin_reference_without_host():
    ref = PluginReference(name="plugin1", version_spec="==1.0.0", host=None)
    assert format_qualified_plugin_reference(ref) == "plugin1==1.0.0"


def test_format_qualified_plugin_reference_bare_name():
    ref = PluginReference(name="plugin1", version_spec="", host=None)
    assert format_qualified_plugin_reference(ref) == "plugin1"
