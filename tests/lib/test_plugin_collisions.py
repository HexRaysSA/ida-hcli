"""Tests for repository-level plugin name collisions and host-aware resolution."""

import io
import json
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import *
from fixtures import PLUGINS_DIR

from hcli.commands.plugin import plugin as plugin_group
from hcli.lib.ida.plugin.exceptions import AmbiguousPluginReferenceError
from hcli.lib.ida.plugin.repo import PluginArchiveIndex, get_plugin_by_name


def make_plugin_zip(
    source_zip: Path,
    dest_path: Path,
    new_name: str | None = None,
    new_version: str | None = None,
    new_repository: str | None = None,
) -> Path:
    """Derive a plugin zip from a fixture by rewriting ida-plugin.json fields.

    Used to synthesize two plugins with the same bare name but different
    repository URLs so we can test collision handling without depending on
    the public Hex-Rays plugin index.
    """
    with zipfile.ZipFile(source_zip, "r") as src, zipfile.ZipFile(dest_path, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename.endswith("ida-plugin.json"):
                metadata = json.loads(data)
                if new_name is not None:
                    metadata["plugin"]["name"] = new_name
                if new_version is not None:
                    metadata["plugin"]["version"] = new_version
                if new_repository is not None:
                    metadata["plugin"]["urls"]["repository"] = new_repository
                data = json.dumps(metadata, indent=2).encode("utf-8")
            dst.writestr(item, data)
    return dest_path


def build_index_with_colliding_plugins(tmp_path: Path) -> PluginArchiveIndex:
    """Build a local index containing two 'shared' plugins from different repos."""
    src = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    # both plugins have identical bare names but different repository URLs
    repo_a_zip = make_plugin_zip(
        src,
        tmp_path / "shared-a.zip",
        new_name="shared",
        new_version="1.0.0",
        new_repository="https://github.com/org-a/shared",
    )
    repo_b_zip = make_plugin_zip(
        src,
        tmp_path / "shared-b.zip",
        new_name="shared",
        new_version="2.0.0",
        new_repository="https://github.com/org-b/shared",
    )

    index = PluginArchiveIndex()
    for zip_path in (repo_a_zip, repo_b_zip):
        buf = zip_path.read_bytes()
        # pass the expected host matching the one in ida-plugin.json so the
        # index accepts the archive (GithubPluginRepo does the same).
        metadata = json.loads(zipfile.ZipFile(io.BytesIO(buf)).read("src-v1/ida-plugin.json"))
        host = metadata["plugin"]["urls"]["repository"]
        index.index_plugin_archive(buf, zip_path.absolute().as_uri(), expected_host=host)
    return index


def test_get_plugin_by_name_unique(tmp_path):
    # build an index with two distinct plugins (no collision)
    src = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    z = make_plugin_zip(src, tmp_path / "alpha.zip", new_name="alpha")
    index = PluginArchiveIndex()
    index.index_plugin_archive(z.read_bytes(), z.absolute().as_uri())

    plugin = get_plugin_by_name(index.get_plugins(), "alpha")
    assert plugin.name == "alpha"


def test_get_plugin_by_name_ambiguous_raises(tmp_path):
    index = build_index_with_colliding_plugins(tmp_path)

    with pytest.raises(AmbiguousPluginReferenceError) as exc_info:
        get_plugin_by_name(index.get_plugins(), "shared")

    err = exc_info.value
    assert err.name == "shared"
    assert len(err.candidates) == 2
    hosts = {host for _, host in err.candidates}
    assert hosts == {
        "https://github.com/org-a/shared",
        "https://github.com/org-b/shared",
    }


def test_get_plugin_by_name_ambiguous_resolved_by_host(tmp_path):
    index = build_index_with_colliding_plugins(tmp_path)
    plugin = get_plugin_by_name(
        index.get_plugins(),
        "shared",
        host="https://github.com/org-a/shared",
    )
    assert plugin.host == "https://github.com/org-a/shared"


def test_get_plugin_by_name_host_normalized(tmp_path):
    """Trailing slashes and casing differences should not prevent matching."""
    index = build_index_with_colliding_plugins(tmp_path)
    plugin = get_plugin_by_name(
        index.get_plugins(),
        "shared",
        host="https://GitHub.com/Org-A/Shared/",
    )
    assert plugin.name == "shared"
    assert plugin.host == "https://github.com/org-a/shared"


def test_get_plugin_by_name_not_found(tmp_path):
    index = build_index_with_colliding_plugins(tmp_path)
    with pytest.raises(KeyError):
        get_plugin_by_name(index.get_plugins(), "does-not-exist")


def test_get_plugin_by_name_is_case_insensitive(tmp_path):
    src = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    z = make_plugin_zip(src, tmp_path / "Foo.zip", new_name="Foo")
    index = PluginArchiveIndex()
    index.index_plugin_archive(z.read_bytes(), z.absolute().as_uri())

    plugin = get_plugin_by_name(index.get_plugins(), "foo")
    assert plugin.name == "Foo"


def _build_colliding_repo_dir(tmp_path: Path) -> Path:
    """Write two colliding-name plugin zips into a repo directory."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    src = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    make_plugin_zip(
        src,
        repo_dir / "shared-a.zip",
        new_name="shared",
        new_version="1.0.0",
        new_repository="https://github.com/org-a/shared",
    )
    make_plugin_zip(
        src,
        repo_dir / "shared-b.zip",
        new_name="shared",
        new_version="2.0.0",
        new_repository="https://github.com/org-b/shared",
    )
    return repo_dir


def test_search_ambiguous_exact_name_renders_candidates(tmp_path, virtual_ida_environment):
    """`plugin search <bare-name>` on an ambiguous name prints candidates and aborts."""
    repo_dir = _build_colliding_repo_dir(tmp_path)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "search", "shared"])

    assert result.exit_code != 0
    assert "plugin name 'shared' is ambiguous" in result.output
    assert "Choose one of:" in result.output
    assert "shared@https://github.com/org-a/shared" in result.output
    assert "shared@https://github.com/org-b/shared" in result.output


def test_search_ambiguous_version_spec_preserves_version(tmp_path, virtual_ida_environment):
    """Candidate suggestions must keep the requested version spec."""
    repo_dir = _build_colliding_repo_dir(tmp_path)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "search", "shared==1.0.0"])

    assert result.exit_code != 0
    assert "plugin name 'shared' is ambiguous" in result.output
    assert "shared==1.0.0@https://github.com/org-a/shared" in result.output
    assert "shared==1.0.0@https://github.com/org-b/shared" in result.output


def test_search_qualified_exact_name_resolves(tmp_path, virtual_ida_environment):
    """A qualified reference picks the right repository plugin."""
    repo_dir = _build_colliding_repo_dir(tmp_path)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        plugin_group,
        ["--repo", str(repo_dir), "search", "shared@https://github.com/org-a/shared"],
    )

    assert result.exit_code == 0, result.output
    # details output should include the plugin name
    assert "shared" in result.output
    # the org-a repo should be identified in metadata
    assert "org-a" in result.output
    # and the org-b version (2.0.0) should not appear in the listing
    assert "2.0.0" not in result.output


def test_search_keyword_matches_colliding_plugins(tmp_path, virtual_ida_environment):
    """Keyword search includes both colliding plugins (substring match on name)."""
    repo_dir = _build_colliding_repo_dir(tmp_path)

    runner = CliRunner(mix_stderr=False)
    # "shar" is a substring of "shared" but not an exact name, so this is a keyword query
    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "search", "shar"])

    assert result.exit_code == 0, result.output
    # both repo URLs should show up as separate rows
    assert "org-a" in result.output
    assert "org-b" in result.output
