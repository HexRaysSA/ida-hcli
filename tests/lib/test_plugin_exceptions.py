from pathlib import Path

from hcli.lib.ida.plugin.exceptions import (
    AmbiguousPluginReferenceError,
    InstalledPluginNameConflictError,
)


class TestAmbiguousPluginReferenceError:
    def test_basic(self):
        candidates = [
            ("foo", "https://github.com/org-a/foo"),
            ("foo", "https://github.com/org-b/foo"),
        ]
        err = AmbiguousPluginReferenceError("foo", candidates)
        assert err.name == "foo"
        assert err.version_spec == ""
        assert err.candidates == candidates

    def test_with_version_spec(self):
        candidates = [("foo", "https://github.com/org-a/foo")]
        err = AmbiguousPluginReferenceError("foo", candidates, version_spec="==1.0.0")
        assert err.version_spec == "==1.0.0"


class TestInstalledPluginNameConflictError:
    def test_basic(self):
        err = InstalledPluginNameConflictError(
            requested_name="foo",
            requested_host="https://github.com/org-b/foo",
            installed_name="foo",
            installed_host="https://github.com/org-a/foo",
            installed_path=Path("/idausr/plugins/foo"),
        )
        assert err.requested_name == "foo"
        assert err.installed_host == "https://github.com/org-a/foo"
        assert "already installed" in str(err)
