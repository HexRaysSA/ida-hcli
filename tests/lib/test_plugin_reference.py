import pytest

from hcli.lib.ida.plugin.reference import (
    PluginReference,
    format_qualified_plugin_reference,
    is_github_direct_install_url,
    is_github_repository_url,
    normalize_plugin_host,
    parse_plugin_reference,
)
from hcli.lib.ida.plugin.repo.github import parse_github_url


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/org/repo",
        "https://github.com/org/repo/",
        "https://github.com/Hex-Rays/ida-hcli",
    ],
)
def test_is_github_repository_url_accepts_valid_repos(value: str):
    assert is_github_repository_url(value)


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


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/org/repo",
        "https://github.com/org/repo/",
        "https://github.com/org/repo.git",
        "https://github.com/org/repo@v1.0",
        "https://github.com/org/repo.git@v1.0",
        "https://github.com/org/repo@release/2.0",
    ],
)
def test_is_github_direct_install_url_accepts(value: str):
    assert is_github_direct_install_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "not a url",
        "github.com/org/repo",
        "http://github.com/org/repo",
        "foo@https://github.com/org/repo",
        "git@github.com:org/repo.git",
    ],
)
def test_is_github_direct_install_url_rejects(value: str):
    assert not is_github_direct_install_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "HTTPS://GitHub.Com/Org/Repo",
        "https://github.com/org/repo/",
        "https://github.com/org/repo",
    ],
)
def test_normalize_plugin_host(value: str):
    assert normalize_plugin_host(value) == "https://github.com/org/repo"


@pytest.mark.parametrize("value", ["not a url", ""])
def test_normalize_plugin_host_rejects_invalid_values(value: str):
    with pytest.raises(ValueError):
        normalize_plugin_host(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plugin1", PluginReference(name="plugin1", version_spec="", host=None)),
        ("plugin1==1.0.0", PluginReference(name="plugin1", version_spec="==1.0.0", host=None)),
        (
            "plugin1@https://github.com/org/repo",
            PluginReference(name="plugin1", version_spec="", host="https://github.com/org/repo"),
        ),
        (
            "plugin1==1.0.0@https://github.com/org/repo",
            PluginReference(name="plugin1", version_spec="==1.0.0", host="https://github.com/org/repo"),
        ),
        # host is normalized: trailing slash stripped, case folded
        (
            "plugin1@https://github.com/org/repo/",
            PluginReference(name="plugin1", version_spec="", host="https://github.com/org/repo"),
        ),
        (
            "plugin1@https://GitHub.com/Org/Repo",
            PluginReference(name="plugin1", version_spec="", host="https://github.com/org/repo"),
        ),
    ],
)
def test_parse_plugin_reference(value: str, expected: PluginReference):
    assert parse_plugin_reference(value) == expected


@pytest.mark.parametrize("op", ["==", ">=", "<=", "!=", "~="])
def test_parse_plugin_reference_various_operators(op: str):
    ref = parse_plugin_reference(f"plugin1{op}1.0.0")
    assert ref.name == "plugin1"
    assert ref.version_spec == f"{op}1.0.0"


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/org/repo",
        "https://github.com/org/repo.git",
        "https://github.com/org/repo@v1.0",
        "https://github.com/org/repo.git@v1.0",
    ],
)
def test_parse_plugin_reference_github_urls_are_not_plugin_references(value: str):
    with pytest.raises(ValueError):
        parse_plugin_reference(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "plugin1=1.0.0",
        "plugin1@https://gitlab.com/org/repo",
        "plugin1@not-a-url",
        "plugin1@https://github.com/org/repo/blob/main",
        "plugin1@@https://github.com/org/repo",
    ],
)
def test_parse_plugin_reference_rejects_invalid_values(value: str):
    with pytest.raises(ValueError):
        parse_plugin_reference(value)


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (
            PluginReference(name="plugin1", version_spec="", host="https://github.com/org/repo"),
            "plugin1@https://github.com/org/repo",
        ),
        (
            PluginReference(name="plugin1", version_spec="==1.0.0", host="https://github.com/org/repo"),
            "plugin1==1.0.0@https://github.com/org/repo",
        ),
        (PluginReference(name="plugin1", version_spec="==1.0.0", host=None), "plugin1==1.0.0"),
        (PluginReference(name="plugin1", version_spec="", host=None), "plugin1"),
    ],
)
def test_format_qualified_plugin_reference_round_trips(ref: PluginReference, expected: str):
    assert format_qualified_plugin_reference(ref) == expected
    # the rendered form is what we show users to disambiguate, so it must parse back
    assert parse_plugin_reference(expected) == ref


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/org/repo", ("org", "repo", None)),
        ("https://github.com/org/repo/", ("org", "repo", None)),
        ("https://github.com/org/repo.git", ("org", "repo", None)),
        ("https://github.com/org/repo@v1.0", ("org", "repo", "v1.0")),
        ("https://github.com/org/repo.git@v1.0", ("org", "repo", "v1.0")),
        ("https://github.com/org/repo@v1.0/", ("org", "repo", "v1.0")),
    ],
)
def test_parse_github_url(url: str, expected: tuple[str, str, str | None]):
    assert parse_github_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/org/repo",
        "https://gitlab.com/org/repo",
        "https://github.com/org/repo@/",
    ],
)
def test_parse_github_url_rejects(url: str):
    with pytest.raises(ValueError):
        parse_github_url(url)
