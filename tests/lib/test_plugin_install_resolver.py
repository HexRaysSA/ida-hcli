"""Tests for `plugin install` selecting the root and its dependencies with the resolver."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import *
from fixtures import temp_env_var

from hcli.commands.plugin import plugin as plugin_group
from hcli.lib.ida import find_current_ida_platform
from hcli.lib.ida.plugin.install import get_installed_plugin_records, get_plugins_directory
from hcli.lib.ida.plugin.repo.bundle import PluginBundleRepo
from hcli.lib.ida.plugin.resolve import Cell, Requirement, resolve

sys.path.insert(0, str(Path(__file__).parent))
from test_plugin_bundle_commands import (
    LINUX_AND_WINDOWS,
    _invoke_create,
    _make_plugin_zip,
    _make_repo_dir,
    _make_suite_zip,
)

CURRENT_PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
NEXT_PYTHON = f"{sys.version_info.major}.{sys.version_info.minor + 1}"
CURRENT_CP = f"cp{sys.version_info.major}{sys.version_info.minor}"


@pytest.fixture
def windows_ida_environment(virtual_ida_environment_with_current_python):
    """`virtual_ida_environment_with_current_python` on the windows-x86_64 platform."""
    with temp_env_var("HCLI_CURRENT_IDA_PLATFORM", "windows-x86_64"):
        yield


def _install(repo: Path, spec: str):
    return CliRunner(mix_stderr=False).invoke(plugin_group, ["--repo", str(repo), "install", spec])


def _get_output(result) -> str:
    return " ".join((result.output + result.stderr).split())


def _get_installed() -> dict[str, str]:
    return {record.name: record.version for record in get_installed_plugin_records()}


def _get_plugins_directory_listing() -> list[str]:
    plugins_dir = get_plugins_directory()
    if not plugins_dir.exists():
        return []
    return sorted(str(path.relative_to(plugins_dir)) for path in plugins_dir.rglob("*"))


def test_install_selects_older_root_when_newest_dependency_is_unavailable(windows_ida_environment, tmp_path):
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "a2.zip": _make_plugin_zip("a", "2.0.0", deps=["b"]),
            "a1.zip": _make_plugin_zip("a", "1.0.0"),
            "b1.zip": _make_plugin_zip("b", "1.0.0", platforms=["linux-x86_64"]),
        },
    )

    result = _install(repo_dir, "a")

    assert result.exit_code == 0, _get_output(result)
    assert _get_installed() == {"a": "1.0.0"}


def test_install_selects_older_dependency_when_its_newest_dependency_is_unavailable(windows_ida_environment, tmp_path):
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "a1.zip": _make_plugin_zip("a", "1.0.0", deps=["b"]),
            "b2.zip": _make_plugin_zip("b", "2.0.0", deps=["c"]),
            "b1.zip": _make_plugin_zip("b", "1.0.0"),
            "c1.zip": _make_plugin_zip("c", "1.0.0", platforms=["linux-x86_64"]),
        },
    )

    result = _install(repo_dir, "a")

    assert result.exit_code == 0, _get_output(result)
    assert _get_installed() == {"a": "1.0.0", "b": "1.0.0"}
    assert "Installed dependency: b" in _get_output(result)


def test_install_selects_dependency_version_by_requires_python(virtual_ida_environment_with_current_python, tmp_path):
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "root.zip": _make_plugin_zip("root", "1.0.0", deps=["a"]),
            "a2.zip": _make_plugin_zip("a", "2.0.0", requires_python=f">={NEXT_PYTHON}"),
            "a19.zip": _make_plugin_zip("a", "1.9.0"),
        },
    )

    result = _install(repo_dir, "root")

    assert result.exit_code == 0, _get_output(result)
    assert _get_installed() == {"root": "1.0.0", "a": "1.9.0"}


def test_install_fails_before_writing_when_required_dependency_cannot_resolve(virtual_ida_environment, tmp_path):
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "a1.zip": _make_plugin_zip("a", "1.0.0", deps=["b", "missing"]),
            "b1.zip": _make_plugin_zip("b", "1.0.0"),
        },
    )
    before = _get_plugins_directory_listing()

    result = _install(repo_dir, "a")

    assert result.exit_code != 0
    assert "Error: cannot resolve a for " in _get_output(result)
    assert "a 1.0.0 needs missing, which is not in the repository" in _get_output(result)
    assert _get_plugins_directory_listing() == before


def test_install_local_archive_fails_before_writing_when_required_dependency_cannot_resolve(
    virtual_ida_environment, tmp_path
):
    repo_dir = _make_repo_dir(tmp_path, {"b1.zip": _make_plugin_zip("b", "1.0.0")})
    local = tmp_path / "a.zip"
    local.write_bytes(_make_plugin_zip("a", "1.0.0", deps=["b", "missing"]))
    before = _get_plugins_directory_listing()

    result = _install(repo_dir, str(local))

    assert result.exit_code != 0
    assert "Error: cannot resolve a 1.0.0 -> missing for " in _get_output(result)
    assert _get_plugins_directory_listing() == before


def test_install_keeps_installed_plugin_that_satisfies_bare_dependency(virtual_ida_environment, tmp_path):
    repo_dir = _make_repo_dir(tmp_path, {"b1.zip": _make_plugin_zip("b", "1.0.0")})
    assert _install(repo_dir, "b").exit_code == 0
    (repo_dir / "b2.zip").write_bytes(_make_plugin_zip("b", "2.0.0"))
    (repo_dir / "a1.zip").write_bytes(_make_plugin_zip("a", "1.0.0", deps=["b"]))
    marker = get_plugins_directory() / "b" / "marker"
    marker.write_text("kept")

    result = _install(repo_dir, "a")

    assert result.exit_code == 0, _get_output(result)
    assert "Skipped dependency: b (already installed)" in _get_output(result)
    assert _get_installed() == {"a": "1.0.0", "b": "1.0.0"}
    assert marker.read_text() == "kept"


def test_install_upgrades_installed_dependency_below_pin(virtual_ida_environment, tmp_path):
    repo_dir = _make_repo_dir(tmp_path, {"b1.zip": _make_plugin_zip("b", "1.0.0")})
    assert _install(repo_dir, "b").exit_code == 0
    (repo_dir / "b2.zip").write_bytes(_make_plugin_zip("b", "2.0.0"))
    (repo_dir / "a1.zip").write_bytes(_make_plugin_zip("a", "1.0.0", deps=["b==2.0.0"]))

    result = _install(repo_dir, "a")

    assert result.exit_code == 0, _get_output(result)
    assert "Upgraded dependency: b" in _get_output(result)
    assert _get_installed() == {"a": "1.0.0", "b": "2.0.0"}


def test_install_keeps_installed_dependency_above_pin(virtual_ida_environment, tmp_path, caplog):
    repo_dir = _make_repo_dir(tmp_path, {"b2.zip": _make_plugin_zip("b", "2.0.0")})
    assert _install(repo_dir, "b").exit_code == 0
    (repo_dir / "b1.zip").write_bytes(_make_plugin_zip("b", "1.0.0"))
    (repo_dir / "a1.zip").write_bytes(_make_plugin_zip("a", "1.0.0", deps=["b==1.0.0"]))

    result = _install(repo_dir, "a")

    assert result.exit_code == 0, _get_output(result)
    assert "Skipped dependency: b (already installed)" in _get_output(result)
    assert _get_installed() == {"a": "1.0.0", "b": "2.0.0"}
    assert any("b 2.0.0 is installed, which is newer than b==1.0.0" in r.getMessage() for r in caplog.records)


def test_install_installs_dependency_of_a_component(virtual_ida_environment, tmp_path):
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "suite.zip": _make_suite_zip("suite", "1.0.0", [("comp", "1.0.0", {"deps": ["b"]})]),
            "b1.zip": _make_plugin_zip("b", "1.0.0"),
        },
    )

    result = _install(repo_dir, "suite")

    assert result.exit_code == 0, _get_output(result)
    assert "Installed dependency: b" in _get_output(result)
    assert _get_installed() == {"suite": "1.0.0", "b": "1.0.0"}


def test_install_reports_already_installed_root_without_upgrade(virtual_ida_environment, tmp_path):
    repo_dir = _make_repo_dir(tmp_path, {"a1.zip": _make_plugin_zip("a", "1.0.0")})
    assert _install(repo_dir, "a").exit_code == 0

    result = _install(repo_dir, "a")

    assert result.exit_code != 0
    assert "Plugin 'a' is already installed" in _get_output(result)


def _make_example_d_repo(tmp_path: Path) -> Path:
    return _make_repo_dir(
        tmp_path,
        {
            "a2.zip": _make_plugin_zip("a", "2.0.0", deps=["b"], platforms=["linux-x86_64", "windows-x86_64"]),
            "a1.zip": _make_plugin_zip("a", "1.0.0"),
            "b1.zip": _make_plugin_zip("b", "1.0.0", platforms=["linux-x86_64"]),
        },
    )


def test_resolve_from_bundle_selects_the_version_bundled_for_the_cell(tmp_path):
    out = tmp_path / "bundle.zip"
    result = _invoke_create(_make_example_d_repo(tmp_path), out, LINUX_AND_WINDOWS, ["a"])
    assert result.exit_code == 0, result.output + result.stderr

    bundle = PluginBundleRepo(out)
    try:
        resolution = resolve([Requirement.from_spec("a")], bundle, Cell("windows-x86_64", python_version="3.12"))
    finally:
        bundle.close()

    assert {name: location.metadata.plugin.version for name, location in resolution.selected.items()} == {"a": "1.0.0"}


def test_install_from_bundle_selects_the_version_bundled_for_the_cell(windows_ida_environment, tmp_path):
    out = tmp_path / "bundle.zip"
    targets = [f"linux-x86_64-{CURRENT_CP}", f"windows-x86_64-{CURRENT_CP}"]
    result = _invoke_create(_make_example_d_repo(tmp_path), out, targets, ["a"])
    assert result.exit_code == 0, result.output + result.stderr

    result = _install(out, "a")

    assert result.exit_code == 0, _get_output(result)
    assert _get_installed() == {"a": "1.0.0"}


def test_install_fetches_dependency_from_configured_bundle_repository(
    virtual_ida_environment_with_current_python, block_network, tmp_path
):
    platform = find_current_ida_platform()
    out = tmp_path / "bundle.zip"
    repo_dir = _make_repo_dir(
        tmp_path,
        {"a1.zip": _make_plugin_zip("a", "1.0.0", deps=["b"]), "b1.zip": _make_plugin_zip("b", "1.0.0")},
    )
    result = _invoke_create(repo_dir, out, [f"{platform}-{CURRENT_CP}"], ["a"])
    assert result.exit_code == 0, result.output + result.stderr
    runner = CliRunner(mix_stderr=False)
    for argv in (
        ["repo", "remove", "hexrays"],
        ["repo", "remove", "community"],
        ["repo", "add", "offline", out.as_uri()],
        ["repo", "set-default", "offline"],
    ):
        assert runner.invoke(plugin_group, argv).exit_code == 0

    result = runner.invoke(plugin_group, ["install", "a"])

    assert result.exit_code == 0, _get_output(result)
    assert _get_installed() == {"a": "1.0.0", "b": "1.0.0"}


def test_install_selects_root_without_requires_python_when_python_cannot_be_detected(
    virtual_ida_environment_without_python, tmp_path
):
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "a2.zip": _make_plugin_zip("a", "2.0.0"),
            "a1.zip": _make_plugin_zip("a", "1.0.0", requires_python=">=3.0"),
        },
    )

    result = _install(repo_dir, "a")

    assert result.exit_code == 0, _get_output(result)
    assert _get_installed() == {"a": "2.0.0"}


def test_install_selects_older_dependency_without_requires_python_when_python_cannot_be_detected(
    virtual_ida_environment_without_python, tmp_path
):
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "a1.zip": _make_plugin_zip("a", "1.0.0", deps=["b"]),
            "b2.zip": _make_plugin_zip("b", "2.0.0", requires_python=">=3.0"),
            "b1.zip": _make_plugin_zip("b", "1.0.0"),
        },
    )

    result = _install(repo_dir, "a")

    assert result.exit_code == 0, _get_output(result)
    assert _get_installed() == {"a": "1.0.0", "b": "1.0.0"}


def test_install_reports_ambiguous_dependency_by_its_own_name(virtual_ida_environment, tmp_path):
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "a1.zip": _make_plugin_zip("a", "1.0.0", deps=["b"]),
            "b-one.zip": _make_plugin_zip("b", "1.0.0", host="https://github.com/one/b"),
            "b-two.zip": _make_plugin_zip("b", "1.0.0", host="https://github.com/two/b"),
        },
    )

    result = _install(repo_dir, "a")

    assert result.exit_code != 0
    assert "plugin name 'a' is ambiguous" not in _get_output(result)
    assert "b is ambiguous" in _get_output(result)
    assert _get_installed() == {}


def test_install_upgrade_without_newer_version_reports_only_the_installed_version(virtual_ida_environment, tmp_path):
    repo_dir = _make_repo_dir(tmp_path, {"a1.zip": _make_plugin_zip("a", "1.0.0")})
    assert _install(repo_dir, "a").exit_code == 0

    result = CliRunner(mix_stderr=False).invoke(plugin_group, ["--repo", str(repo_dir), "install", "-U", "a"])

    assert result.exit_code == 0, _get_output(result)
    assert "Already installed plugin: a==1.0.0" in _get_output(result)
    assert "newer versions" not in _get_output(result)


def test_install_explains_that_no_version_of_the_root_supports_the_environment(windows_ida_environment, tmp_path):
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "a2.zip": _make_plugin_zip("a", "2.0.0", platforms=["linux-x86_64"]),
            "a1.zip": _make_plugin_zip("a", "1.0.0", platforms=["linux-x86_64"]),
        },
    )

    result = _install(repo_dir, "a")

    assert result.exit_code != 0
    assert "a 2.0.0 and 1 older version do not support windows-x86_64" in _get_output(result)
    assert "not found" not in _get_output(result)
