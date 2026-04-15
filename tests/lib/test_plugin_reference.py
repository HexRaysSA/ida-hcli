import pytest

from hcli.lib.ida.plugin.reference import (
    PluginReference,
    format_qualified_plugin_reference,
    is_github_repository_url,
    normalize_plugin_host,
    parse_plugin_reference,
)


class TestIsGithubRepositoryUrl:
    def test_valid(self):
        assert is_github_repository_url("https://github.com/org/repo")
        assert is_github_repository_url("https://github.com/org/repo/")
        assert is_github_repository_url("https://github.com/Hex-Rays/ida-hcli")

    def test_rejects_subpaths(self):
        # trailing segments beyond owner/repo are not valid repo URLs
        assert not is_github_repository_url("https://github.com/org/repo/blob/main")

    def test_rejects_loose_matches(self):
        assert not is_github_repository_url("not a url")
        assert not is_github_repository_url("github.com/org/repo")
        assert not is_github_repository_url("http://github.com/org/repo")
        # the old is_github_url() would accept substrings like these
        assert not is_github_repository_url("foo@https://github.com/org/repo")
        assert not is_github_repository_url("prefix https://github.com/org/repo")


class TestNormalizePluginHost:
    def test_lowercases_components(self):
        assert normalize_plugin_host("HTTPS://GitHub.Com/Org/Repo") == "https://github.com/org/repo"

    def test_strips_trailing_slash(self):
        assert normalize_plugin_host("https://github.com/org/repo/") == "https://github.com/org/repo"

    def test_preserves_no_trailing_slash(self):
        assert normalize_plugin_host("https://github.com/org/repo") == "https://github.com/org/repo"

    def test_rejects_invalid(self):
        with pytest.raises(ValueError):
            normalize_plugin_host("not a url")
        with pytest.raises(ValueError):
            normalize_plugin_host("")


class TestParsePluginReference:
    def test_bare_name(self):
        ref = parse_plugin_reference("plugin1")
        assert ref == PluginReference(name="plugin1", version_spec="", host=None)

    def test_bare_version(self):
        ref = parse_plugin_reference("plugin1==1.0.0")
        assert ref == PluginReference(name="plugin1", version_spec="==1.0.0", host=None)

    def test_various_operators(self):
        for op in ("==", ">=", "<=", "!=", "~="):
            ref = parse_plugin_reference(f"plugin1{op}1.0.0")
            assert ref.name == "plugin1"
            assert ref.version_spec == f"{op}1.0.0"

    def test_qualified_name(self):
        ref = parse_plugin_reference("plugin1@https://github.com/org/repo")
        assert ref == PluginReference(
            name="plugin1",
            version_spec="",
            host="https://github.com/org/repo",
        )

    def test_qualified_version(self):
        ref = parse_plugin_reference("plugin1==1.0.0@https://github.com/org/repo")
        assert ref == PluginReference(
            name="plugin1",
            version_spec="==1.0.0",
            host="https://github.com/org/repo",
        )

    def test_qualified_trailing_slash_is_normalized(self):
        ref = parse_plugin_reference("plugin1@https://github.com/org/repo/")
        assert ref.host == "https://github.com/org/repo"

    def test_qualified_case_insensitive(self):
        ref = parse_plugin_reference("plugin1@https://GitHub.com/Org/Repo")
        assert ref.host == "https://github.com/org/repo"

    def test_raw_github_url_is_not_a_plugin_reference(self):
        # distinct from qualified references; raw URLs are handled by install
        # as "install from that repository", so parsing must reject them.
        with pytest.raises(ValueError):
            parse_plugin_reference("https://github.com/org/repo")

    def test_empty(self):
        with pytest.raises(ValueError):
            parse_plugin_reference("")

    def test_bad_operator(self):
        with pytest.raises(ValueError):
            parse_plugin_reference("plugin1=1.0.0")


class TestFormatQualifiedPluginReference:
    def test_name_only(self):
        ref = PluginReference(name="plugin1", version_spec="", host="https://github.com/org/repo")
        assert format_qualified_plugin_reference(ref) == "plugin1@https://github.com/org/repo"

    def test_with_version(self):
        ref = PluginReference(name="plugin1", version_spec="==1.0.0", host="https://github.com/org/repo")
        assert format_qualified_plugin_reference(ref) == "plugin1==1.0.0@https://github.com/org/repo"

    def test_without_host(self):
        ref = PluginReference(name="plugin1", version_spec="==1.0.0", host=None)
        assert format_qualified_plugin_reference(ref) == "plugin1==1.0.0"

    def test_tuple_input(self):
        assert (
            format_qualified_plugin_reference(("plugin1", "==1.0.0", "https://github.com/org/repo"))
            == "plugin1==1.0.0@https://github.com/org/repo"
        )
