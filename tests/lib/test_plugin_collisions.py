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


def test_install_ambiguous_bare_name_fails(tmp_path, virtual_ida_environment):
    """`plugin install <bare-name>` on an ambiguous name prints candidates and aborts."""
    repo_dir = _build_colliding_repo_dir(tmp_path)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "install", "shared"])

    assert result.exit_code != 0
    assert "plugin name 'shared' is ambiguous" in result.output
    assert "shared@https://github.com/org-a/shared" in result.output
    assert "shared@https://github.com/org-b/shared" in result.output


def test_install_qualified_name_succeeds(tmp_path, virtual_ida_environment):
    """`plugin install name@repo` installs the selected repository plugin."""
    from hcli.lib.ida.plugin.install import is_plugin_installed

    repo_dir = _build_colliding_repo_dir(tmp_path)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        plugin_group,
        ["--repo", str(repo_dir), "install", "shared@https://github.com/org-a/shared"],
    )

    assert result.exit_code == 0, result.output
    assert is_plugin_installed("shared")


def _build_colliding_repo_dir_with_v2(tmp_path: Path) -> Path:
    """Colliding repo where org-a also publishes a newer version available to upgrade to."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    src = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    # two versions of shared@org-a: v1.0.0 (installable first) and v3.0.0 (upgrade target)
    make_plugin_zip(
        src,
        repo_dir / "shared-a-v1.zip",
        new_name="shared",
        new_version="1.0.0",
        new_repository="https://github.com/org-a/shared",
    )
    make_plugin_zip(
        src,
        repo_dir / "shared-a-v3.zip",
        new_name="shared",
        new_version="3.0.0",
        new_repository="https://github.com/org-a/shared",
    )
    # and one version of shared@org-b to force name-only ambiguity in the repo
    make_plugin_zip(
        src,
        repo_dir / "shared-b-v1.zip",
        new_name="shared",
        new_version="2.0.0",
        new_repository="https://github.com/org-b/shared",
    )
    return repo_dir


def test_upgrade_bare_name_uses_installed_host(tmp_path, virtual_ida_environment):
    """`plugin upgrade <bare-name>` anchors on the installed plugin's host."""
    repo_dir = _build_colliding_repo_dir_with_v2(tmp_path)
    runner = CliRunner(mix_stderr=False)

    # install v1.0.0 of shared@org-a
    result = runner.invoke(
        plugin_group,
        ["--repo", str(repo_dir), "install", "shared==1.0.0@https://github.com/org-a/shared"],
    )
    assert result.exit_code == 0, result.output

    # now upgrade by bare name — should pick v3.0.0 from org-a, not the v2.0.0 from org-b
    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "upgrade", "shared"])
    assert result.exit_code == 0, result.output
    assert "3.0.0" in result.output


def test_upgrade_host_mismatch_fails(tmp_path, virtual_ida_environment):
    """Upgrade must not switch an installed plugin from one repository to another."""
    repo_dir = _build_colliding_repo_dir_with_v2(tmp_path)
    runner = CliRunner(mix_stderr=False)

    # install shared@org-a v1.0.0
    result = runner.invoke(
        plugin_group,
        ["--repo", str(repo_dir), "install", "shared==1.0.0@https://github.com/org-a/shared"],
    )
    assert result.exit_code == 0, result.output

    # try to "upgrade" by pointing at org-b — should fail with a repo-switch error
    result = runner.invoke(
        plugin_group,
        ["--repo", str(repo_dir), "upgrade", "shared@https://github.com/org-b/shared"],
    )
    assert result.exit_code != 0
    assert "comes from https://github.com/org-a/shared" in result.output
    assert "Upgrade cannot switch repositories" in result.output


def test_uninstall_case_insensitive_cli(tmp_path, virtual_ida_environment):
    """`plugin uninstall PLUGIN1` finds $IDAUSR/plugins/plugin1."""
    from hcli.lib.ida.plugin.install import install_plugin_archive, is_plugin_installed

    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")
    assert is_plugin_installed("plugin1")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["uninstall", "PLUGIN1"])
    assert result.exit_code == 0, result.output
    assert not is_plugin_installed("plugin1")


def test_config_list_case_insensitive_cli(tmp_path, virtual_ida_environment):
    """`plugin config PLUGIN1 list` resolves to the installed plugin's directory."""
    from hcli.lib.ida.plugin.install import install_plugin_archive

    # plugin1 has no settings defined, so `config list` should show the "No settings defined" line
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["config", "PLUGIN1", "list"])
    assert result.exit_code == 0, result.output
    assert "No settings defined" in result.output


def test_status_does_not_crash_on_colliding_name(tmp_path, virtual_ida_environment):
    """status must succeed even when the installed plugin's bare name collides in the repository."""
    repo_dir = _build_colliding_repo_dir_with_v2(tmp_path)
    runner = CliRunner(mix_stderr=False)

    # install shared@org-a v1.0.0
    result = runner.invoke(
        plugin_group,
        ["--repo", str(repo_dir), "install", "shared==1.0.0@https://github.com/org-a/shared"],
    )
    assert result.exit_code == 0, result.output

    # status should not fail because of the repo-side name collision
    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "status"])
    assert result.exit_code == 0, result.output
    assert "shared" in result.output
    # should detect the v3.0.0 upgrade from org-a, not v2.0.0 from org-b
    assert "3.0.0" in result.output


def test_upgrade_not_installed_fails(tmp_path, virtual_ida_environment):
    """Upgrade must fail cleanly when the plugin is not installed."""
    repo_dir = _build_colliding_repo_dir_with_v2(tmp_path)
    runner = CliRunner(mix_stderr=False)

    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "upgrade", "shared"])
    assert result.exit_code != 0
    assert "not installed" in result.output


def test_install_same_name_conflict(tmp_path, virtual_ida_environment):
    """Installing a same-name plugin from another repository must fail with a conflict error."""
    repo_dir = _build_colliding_repo_dir(tmp_path)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        plugin_group,
        ["--repo", str(repo_dir), "install", "shared@https://github.com/org-a/shared"],
    )
    assert result.exit_code == 0, result.output

    # now try to install the other colliding plugin
    result = runner.invoke(
        plugin_group,
        ["--repo", str(repo_dir), "install", "shared@https://github.com/org-b/shared"],
    )
    assert result.exit_code != 0
    assert "cannot install plugin" in result.output
    assert "https://github.com/org-a/shared" in result.output
    assert "https://github.com/org-b/shared" in result.output
    assert "Only one plugin with the bare name 'shared' can be installed at a time." in result.output
