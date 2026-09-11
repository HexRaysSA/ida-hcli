import pytest

from hcli.lib.ida.plugin.reference import (
    PluginReference,
    format_qualified_plugin_reference,
    is_github_direct_install_url,
    is_github_repository_url,
    is_plugin_host_url,
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


# --- Hex-Rays portal identities ------------------------------------------------
#
# A plugin distributed through the portal is built from a private GitHub
# repository, so its github.com URL cannot be its public identity. The portal
# page stands in, and hcli must both accept it as an identity and parse a
# reference to it -- a reference hcli cannot parse is a plugin nobody can name.


@pytest.mark.parametrize(
    "value",
    [
        # canonical: <org>/<repo>/<name>
        "https://plugins.hex-rays.com/org/repo/plugin1",
        "https://plugins.hex-rays.com/org/repo/plugin1/",
        # two segments: predates the canonical shape and is still installed on
        # user machines, so it must keep parsing
        "https://plugins.hex-rays.com/org/repo",
        # GitHub keeps working through the same predicate
        "https://github.com/org/repo",
    ],
)
def test_is_plugin_host_url_accepts_identity_shapes(value: str):
    assert is_plugin_host_url(value)


@pytest.mark.parametrize(
    "value",
    [
        # one segment is not an identity
        "https://plugins.hex-rays.com/org",
        # four is past the namespace
        "https://plugins.hex-rays.com/a/b/c/d",
        # subdomains are delivery hosts, never identities: allowing them would
        # let one plugin hold two identities that never collide
        "https://hexrays.plugins.hex-rays.com/org/repo/plugin1",
        "https://evil.plugins.hex-rays.com/a/b",
        # a look-alike host outside the namespace
        "https://plugins.hex-rays.com.attacker.tld/a/b",
        "http://plugins.hex-rays.com/a/b",
        "https://gitlab.com/org/repo",
    ],
)
def test_is_plugin_host_url_rejects_non_identities(value: str):
    assert not is_plugin_host_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "https://plugins.hex-rays.com/org/repo/plugin1",
        "https://plugins.hex-rays.com/org/repo",
    ],
)
def test_portal_urls_are_not_direct_install_urls(value: str):
    # is_github_direct_install_url guards "this string is a raw URL, not a
    # reference". Widening it to the portal would make `hcli plugin install
    # <portal-url>` try to install from the page instead of resolving a
    # reference, so it stays GitHub-only.
    assert not is_github_direct_install_url(value)


def test_parse_plugin_reference_accepts_portal_host():
    ref = parse_plugin_reference("plugin1@https://plugins.hex-rays.com/org/repo/plugin1")
    assert ref == PluginReference(
        name="plugin1",
        version_spec="",
        host="https://plugins.hex-rays.com/org/repo/plugin1",
        repo=None,
    )


def test_parse_plugin_reference_normalizes_portal_host_case():
    # users type references by hand; a descriptor's casing must not decide
    # whether the reference resolves
    ref = parse_plugin_reference("plugin1@https://PLUGINS.hex-rays.com/Org/Repo")
    assert ref.host == "https://plugins.hex-rays.com/org/repo"


# --- repository prefixes -------------------------------------------------------
#
# A leading "repo/" selects which repository to search. It is lookup scope only:
# dropped once the plugin resolves, never stored, because where a plugin was
# found is not part of what it is.


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("hexrays/foo", PluginReference(name="foo", version_spec="", host=None, repo="hexrays")),
        ("community/foo", PluginReference(name="foo", version_spec="", host=None, repo="community")),
        # digits and hyphens are legal in a repository name
        ("my-repo-2/foo", PluginReference(name="foo", version_spec="", host=None, repo="my-repo-2")),
        # version specs compose with the prefix
        ("hexrays/foo>=1.2", PluginReference(name="foo", version_spec=">=1.2", host=None, repo="hexrays")),
        ("hexrays/foo==1.0.0", PluginReference(name="foo", version_spec="==1.0.0", host=None, repo="hexrays")),
        # prefix and host together: the prefix scopes the search, the host pins
        # identity
        (
            "hexrays/plugin1@https://plugins.hex-rays.com/org/repo/plugin1",
            PluginReference(
                name="plugin1",
                version_spec="",
                host="https://plugins.hex-rays.com/org/repo/plugin1",
                repo="hexrays",
            ),
        ),
        (
            "hexrays/plugin1>=1.2@https://plugins.hex-rays.com/org/repo/plugin1",
            PluginReference(
                name="plugin1",
                version_spec=">=1.2",
                host="https://plugins.hex-rays.com/org/repo/plugin1",
                repo="hexrays",
            ),
        ),
        # a prefix may also scope a GitHub-identified plugin
        (
            "community/foo@https://github.com/org/repo",
            PluginReference(name="foo", version_spec="", host="https://github.com/org/repo", repo="community"),
        ),
    ],
)
def test_parse_plugin_reference_with_repo_prefix(value: str, expected: PluginReference):
    assert parse_plugin_reference(value) == expected


def test_unprefixed_reference_has_no_repo():
    assert parse_plugin_reference("foo").repo is None


@pytest.mark.parametrize(
    "value",
    [
        # repository names are lowercase; an uppercase prefix is not a prefix,
        # so the slash lands in the name and is refused
        "HEXRAYS/foo",
        "Hexrays/foo",
        # underscores are legal in plugin names but not repository names
        "my_repo/foo",
        # only one prefix level exists
        "a/b/c",
        # empty on either side of the separator
        "/foo",
        "foo/",
    ],
)
def test_parse_plugin_reference_rejects_bad_prefixes(value: str):
    with pytest.raises(ValueError):
        parse_plugin_reference(value)


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/org/repo",
        "https://plugins.hex-rays.com/org/repo/plugin1",
        "file:///tmp/plugin.zip",
    ],
)
def test_repo_prefix_cannot_swallow_a_url_scheme(value: str):
    # The prefix pattern requires every character before the first "/" to be
    # [a-z0-9-], which a scheme's ":" fails. Without that, "https://host/path"
    # would parse as repository "https:".
    try:
        ref = parse_plugin_reference(value)
    except ValueError:
        return  # refused outright, which is also correct
    assert ref.repo is None


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (PluginReference(name="foo", version_spec="", host=None, repo="hexrays"), "hexrays/foo"),
        (PluginReference(name="foo", version_spec=">=1.2", host=None, repo="hexrays"), "hexrays/foo>=1.2"),
        (
            PluginReference(
                name="plugin1",
                version_spec="==1.0.0",
                host="https://plugins.hex-rays.com/org/repo/plugin1",
                repo="hexrays",
            ),
            "hexrays/plugin1==1.0.0@https://plugins.hex-rays.com/org/repo/plugin1",
        ),
    ],
)
def test_format_qualified_plugin_reference_round_trips_with_repo(ref: PluginReference, expected: str):
    assert format_qualified_plugin_reference(ref) == expected
    # search prints this string as the thing to type; it must parse back
    assert parse_plugin_reference(expected) == ref
