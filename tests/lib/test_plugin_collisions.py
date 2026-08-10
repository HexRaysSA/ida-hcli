"""Tests for repository-level plugin name collisions and host-aware resolution."""

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
        index.index_plugin_archive(zip_path.read_bytes(), zip_path.absolute().as_uri())
    return index


@pytest.mark.parametrize(
    "indexed_name,query",
    [
        ("alpha", "alpha"),
        ("Foo", "foo"),
    ],
)
def test_get_plugin_by_name_matches_case_insensitively(tmp_path, indexed_name, query):
    z = make_plugin_zip(PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip", tmp_path / "p.zip", new_name=indexed_name)
    index = PluginArchiveIndex()
    index.index_plugin_archive(z.read_bytes(), z.absolute().as_uri())

    plugin = get_plugin_by_name(index.get_plugins(), query)
    assert plugin.name == indexed_name


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


@pytest.mark.parametrize(
    "host",
    [
        "https://github.com/org-a/shared",
        # trailing slashes and casing differences should not prevent matching
        "https://GitHub.com/Org-A/Shared/",
    ],
)
def test_get_plugin_by_name_ambiguous_resolved_by_host(tmp_path, host):
    index = build_index_with_colliding_plugins(tmp_path)
    plugin = get_plugin_by_name(index.get_plugins(), "shared", host=host)
    assert plugin.name == "shared"
    assert plugin.host == "https://github.com/org-a/shared"


def test_index_normalizes_hosts_during_indexing(tmp_path):
    src = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    repo_a_zip = make_plugin_zip(
        src,
        tmp_path / "shared-a.zip",
        new_name="shared",
        new_version="1.0.0",
        new_repository="https://github.com/Org-A/Shared",
    )
    repo_b_zip = make_plugin_zip(
        src,
        tmp_path / "shared-b.zip",
        new_name="shared",
        new_version="2.0.0",
        new_repository="https://github.com/org-a/shared/",
    )

    index = PluginArchiveIndex()
    index.index_plugin_archive(repo_a_zip.read_bytes(), repo_a_zip.absolute().as_uri())
    index.index_plugin_archive(repo_b_zip.read_bytes(), repo_b_zip.absolute().as_uri())

    plugins = index.get_plugins()
    assert len(plugins) == 1
    plugin = get_plugin_by_name(plugins, "shared", host="https://github.com/org-a/shared")
    assert plugin.host == "https://github.com/org-a/shared"
    assert set(plugin.versions) == {"1.0.0", "2.0.0"}


def test_get_plugin_by_name_not_found(tmp_path):
    index = build_index_with_colliding_plugins(tmp_path)
    with pytest.raises(KeyError):
        get_plugin_by_name(index.get_plugins(), "does-not-exist")


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


@pytest.mark.parametrize("version_spec", ["", "==1.0.0", ">=1.0.0"])
def test_search_ambiguous_renders_candidates(tmp_path, virtual_ida_environment, version_spec):
    """`plugin search <bare-name>` aborts and suggests qualified references, keeping the version spec."""
    repo_dir = _build_colliding_repo_dir(tmp_path)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "search", f"shared{version_spec}"])

    assert result.exit_code != 0
    assert "plugin name 'shared' is ambiguous" in result.output
    assert f"shared{version_spec}@https://github.com/org-a/shared" in result.output
    assert f"shared{version_spec}@https://github.com/org-b/shared" in result.output


def test_search_qualified_exact_name_resolves(tmp_path, virtual_ida_environment):
    """A qualified reference picks the right repository plugin."""
    repo_dir = _build_colliding_repo_dir(tmp_path)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        plugin_group,
        ["--repo", str(repo_dir), "search", "shared@https://github.com/org-a/shared"],
    )

    assert result.exit_code == 0, result.output
    assert "shared" in result.output
    # the org-a repo should be identified in metadata
    assert "org-a" in result.output
    # and the org-b version (2.0.0) should not appear in the listing
    assert "2.0.0" not in result.output


@pytest.mark.parametrize(
    "query,expected",
    [
        # "shar" is a substring of "shared" but not an exact name, so this is a keyword query,
        # and both colliding plugins should show up as separate rows.
        ("shar", ["org-a", "org-b"]),
        ("does-not-match-anything", ["No plugins found"]),
    ],
)
def test_search_keyword_query(tmp_path, virtual_ida_environment, query, expected):
    repo_dir = _build_colliding_repo_dir(tmp_path)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "search", query])

    assert result.exit_code == 0, result.output
    for fragment in expected:
        assert fragment in result.output


@pytest.mark.parametrize(
    "spec,expect_success,present,absent",
    [
        ("plugin1==2.0.0", True, ["download locations:"], ["matching versions:"]),
        ("plugin1>=2.0.0", True, ["matching versions:", "5.0.0", "2.0.0"], ["download locations:", "1.0.0"]),
        ("plugin1!=1.0.0", True, ["matching versions:", "2.0.0"], ["1.0.0"]),
        ("plugin1>=99.0.0", False, ["no versions matching '>=99.0.0' found for plugin 'plugin1'"], []),
    ],
)
def test_search_version_spec(virtual_ida_environment, spec, expect_success, present, absent):
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["--repo", str(PLUGINS_DIR), "search", spec])

    if expect_success:
        assert result.exit_code == 0, result.output
    else:
        assert result.exit_code != 0

    for fragment in present:
        assert fragment in result.output
    for fragment in absent:
        assert fragment not in result.output


def test_search_json_keyword_query(virtual_ida_environment):
    """`search --json` (no query) reports the full plugin listing as JSON."""
    runner = CliRunner(mix_stderr=False)

    result = runner.invoke(plugin_group, ["--repo", str(PLUGINS_DIR), "search", "--json"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output)
    assert payload["query"] is None
    names = {entry["name"] for entry in payload["results"]}
    assert "plugin1" in names


def test_search_json_exact_name_query(virtual_ida_environment):
    """`search <name> --json` reports plugin metadata and versions as JSON."""
    runner = CliRunner(mix_stderr=False)

    result = runner.invoke(plugin_group, ["--repo", str(PLUGINS_DIR), "search", "plugin1", "--json"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output)
    assert payload["plugin"]["name"] == "plugin1"
    assert payload["installed_version"] is None
    assert any(v["version"] == "4.0.0" for v in payload["versions"])


def test_search_json_ambiguous_reference(tmp_path, virtual_ida_environment):
    """`search <name> --json` reports an ambiguity error as JSON and exits non-zero."""
    repo_dir = _build_colliding_repo_dir(tmp_path)
    runner = CliRunner(mix_stderr=False)

    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "search", "shared", "--json"])
    assert result.exit_code != 0

    payload = json.loads(result.output)
    assert payload["error"] == "ambiguous plugin reference"
    assert payload["name"] == "shared"
    assert len(payload["candidates"]) == 2


def test_install_ambiguous_bare_name_fails(tmp_path, virtual_ida_environment):
    """`plugin install <bare-name>` on an ambiguous name prints candidates and aborts."""
    repo_dir = _build_colliding_repo_dir(tmp_path)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "install", "shared"])

    assert result.exit_code != 0
    assert "plugin name 'shared' is ambiguous" in result.output
    assert "shared@https://github.com/org-a/shared" in result.output
    assert "shared@https://github.com/org-b/shared" in result.output


def test_install_same_name_conflict(tmp_path, virtual_ida_environment):
    """Installing a same-name plugin from another repository must fail with a conflict error."""
    from hcli.lib.ida.plugin.install import is_plugin_installed

    repo_dir = _build_colliding_repo_dir(tmp_path)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        plugin_group,
        ["--repo", str(repo_dir), "install", "shared@https://github.com/org-a/shared"],
    )
    assert result.exit_code == 0, result.output
    assert is_plugin_installed("shared")

    # now try to install the other colliding plugin
    result = runner.invoke(
        plugin_group,
        ["--repo", str(repo_dir), "install", "shared@https://github.com/org-b/shared"],
    )
    assert result.exit_code != 0
    assert "cannot install plugin" in result.output
    assert "https://github.com/org-a/shared" in result.output
    assert "https://github.com/org-b/shared" in result.output


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


def test_upgrade_not_installed_fails(tmp_path, virtual_ida_environment):
    """Upgrade must fail cleanly when the plugin is not installed."""
    repo_dir = _build_colliding_repo_dir_with_v2(tmp_path)
    runner = CliRunner(mix_stderr=False)

    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "upgrade", "shared"])
    assert result.exit_code != 0
    assert "not installed" in result.output


def test_uninstall_case_insensitive_cli(virtual_ida_environment):
    """`plugin uninstall PLUGIN1` finds $IDAUSR/plugins/plugin1."""
    from hcli.lib.ida.plugin.install import install_plugin_archive, is_plugin_installed

    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")
    assert is_plugin_installed("plugin1")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["uninstall", "PLUGIN1"])
    assert result.exit_code == 0, result.output
    assert not is_plugin_installed("plugin1")


def test_config_case_insensitive_cli(virtual_ida_environment):
    """`plugin config PLUGIN1 ...` resolves to the installed plugin's directory and record."""
    from hcli.lib.ida.plugin.install import install_plugin_archive
    from hcli.lib.ida.plugin.settings import set_plugin_setting

    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v5.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")
    set_plugin_setting("plugin1", "key1", "value")

    runner = CliRunner(mix_stderr=False)

    # `list` resolves the installed plugin directory
    result = runner.invoke(plugin_group, ["config", "PLUGIN1", "list"])
    assert result.exit_code == 0, result.output
    assert "key1" in result.output
    assert "value" in result.output

    # `export` resolves the installed plugin record
    result = runner.invoke(plugin_group, ["config", "PLUGIN1", "export"])
    assert result.exit_code == 0, result.output
    assert '"key1": "value"' in result.output


def test_config_export_requires_installed_plugin(virtual_ida_environment):
    from hcli.lib.ida.plugin.install import install_plugin_archive, uninstall_plugin
    from hcli.lib.ida.plugin.settings import set_plugin_setting

    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v5.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")
    set_plugin_setting("plugin1", "key1", "value")
    uninstall_plugin("plugin1")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["config", "plugin1", "export"])
    assert result.exit_code != 0
    assert "not installed" in result.output


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


def test_status_skip_upgrade_check(virtual_ida_environment):
    """`status --skip-upgrade-check` reports installed plugins without querying the repo for upgrades."""
    runner = CliRunner(mix_stderr=False)

    result = runner.invoke(plugin_group, ["--repo", str(PLUGINS_DIR), "install", "plugin1==1.0.0"])
    assert result.exit_code == 0, result.output

    # sanity check: without the flag, status detects the newer version available in PLUGINS_DIR
    online_result = runner.invoke(plugin_group, ["--repo", str(PLUGINS_DIR), "status", "plugin1"])
    assert online_result.exit_code == 0, online_result.output
    assert "upgradable to" in online_result.output

    skip_result = runner.invoke(plugin_group, ["--repo", str(PLUGINS_DIR), "status", "plugin1", "--skip-upgrade-check"])
    assert skip_result.exit_code == 0, skip_result.output
    assert "plugin1" in skip_result.output
    assert "upgradable to" not in skip_result.output
    assert "skipped" in skip_result.output


@pytest.mark.parametrize(
    "extra_args,upgrade_checked,in_repository,upgradable_to",
    [
        ([], True, True, "6.0.0"),
        (["--skip-upgrade-check"], False, None, None),
    ],
)
def test_status_json_installed_plugin(
    virtual_ida_environment, extra_args, upgrade_checked, in_repository, upgradable_to
):
    """`status <name> --json` reports upgrade info as structured JSON."""
    runner = CliRunner(mix_stderr=False)

    result = runner.invoke(plugin_group, ["--repo", str(PLUGINS_DIR), "install", "plugin1==1.0.0"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(plugin_group, ["--repo", str(PLUGINS_DIR), "status", "plugin1", "--json", *extra_args])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output)
    assert payload["plugins"] == [
        {
            "name": "plugin1",
            "version": "1.0.0",
            "installed": True,
            "kind": "installed",
            "upgrade_checked": upgrade_checked,
            "in_repository": in_repository,
            "upgradable_to": upgradable_to,
        }
    ]


def test_status_json_not_installed_plugin(virtual_ida_environment):
    """`status <name> --json` reports a missing plugin and exits non-zero."""
    runner = CliRunner(mix_stderr=False)

    result = runner.invoke(plugin_group, ["--repo", str(PLUGINS_DIR), "status", "does-not-exist", "--json"])
    assert result.exit_code != 0

    payload = json.loads(result.output)
    assert payload["plugins"] == [{"name": "does-not-exist", "installed": False}]


def test_status_multiple_plugins_all_installed(virtual_ida_environment):
    """`status <name1> <name2>` shows both plugins and exits 0 when both are installed."""
    runner = CliRunner(mix_stderr=False)

    result = runner.invoke(plugin_group, ["--repo", str(PLUGINS_DIR), "install", "plugin1==1.0.0"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(plugin_group, ["--repo", str(PLUGINS_DIR), "install", "zydisinfo==1.0.0"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(plugin_group, ["--repo", str(PLUGINS_DIR), "status", "plugin1", "zydisinfo"])
    assert result.exit_code == 0, result.output
    assert "plugin1" in result.output
    assert "zydisinfo" in result.output


def test_status_multiple_plugins_partially_installed(virtual_ida_environment):
    """`status <name1> <name2>` exits non-zero and reports only the missing one when mixed."""
    runner = CliRunner(mix_stderr=False)

    result = runner.invoke(plugin_group, ["--repo", str(PLUGINS_DIR), "install", "plugin1==1.0.0"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(plugin_group, ["--repo", str(PLUGINS_DIR), "status", "plugin1", "does-not-exist"])
    assert result.exit_code != 0
    assert "plugin1" in result.output
    assert "Not installed" in result.output
    assert "does-not-exist" in result.output
