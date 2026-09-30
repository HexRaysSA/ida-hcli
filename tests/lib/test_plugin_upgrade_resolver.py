"""Tests for `plugin upgrade` selecting the newest viable version with the resolver."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import *
from fixtures import temp_env_var

from hcli.commands.plugin import plugin as plugin_group
from hcli.lib.ida import find_current_ida_platform
from hcli.lib.ida.plugin.install import get_installed_plugin_records

sys.path.insert(0, str(Path(__file__).parent))
from test_plugin_bundle_commands import _invoke_create, _make_plugin_zip, _make_repo_dir

NEXT_PYTHON = f"{sys.version_info.major}.{sys.version_info.minor + 1}"
CURRENT_CP = f"cp{sys.version_info.major}{sys.version_info.minor}"


@pytest.fixture
def windows_ida_environment(virtual_ida_environment_with_current_python):
    """`virtual_ida_environment_with_current_python` on the windows-x86_64 platform."""
    with temp_env_var("HCLI_CURRENT_IDA_PLATFORM", "windows-x86_64"):
        yield


def _invoke(*argv: str):
    return CliRunner(mix_stderr=False).invoke(plugin_group, list(argv))


def _get_output(result) -> str:
    return " ".join((result.output + result.stderr).split())


def _get_installed() -> dict[str, str]:
    return {record.name: record.version for record in get_installed_plugin_records()}


def _make_named_repo_dir(tmp_path: Path, name: str, archives: dict[str, bytes]) -> Path:
    (tmp_path / name).mkdir()
    return _make_repo_dir(tmp_path / name, archives)


def _install_from(tmp_path: Path, name: str, archives: dict[str, bytes], spec: str) -> None:
    repo_dir = _make_named_repo_dir(tmp_path, name, archives)
    result = _invoke("--repo", str(repo_dir), "install", spec)
    assert result.exit_code == 0, _get_output(result)


def test_upgrade_reports_no_viable_upgrade_when_newest_dependency_is_unavailable(windows_ida_environment, tmp_path):
    archives = {
        "a2.zip": _make_plugin_zip("a", "2.0.0", deps=["b"]),
        "a1.zip": _make_plugin_zip("a", "1.0.0"),
        "b1.zip": _make_plugin_zip("b", "1.0.0", platforms=["linux-x86_64"]),
    }
    _install_from(tmp_path, "installed", archives, "a")
    repo_dir = _make_named_repo_dir(tmp_path, "repo", archives)

    result = _invoke("--repo", str(repo_dir), "upgrade", "a")

    assert result.exit_code == 0, _get_output(result)
    output = _get_output(result)
    assert "a is already up to date (1.0.0)" in output
    assert "a 2.0.0 needs b" in output
    assert _get_installed() == {"a": "1.0.0"}


def test_upgrade_selects_newest_viable_version(windows_ida_environment, tmp_path):
    _install_from(tmp_path, "installed", {"a1.zip": _make_plugin_zip("a", "1.0.0")}, "a")
    repo_dir = _make_named_repo_dir(
        tmp_path,
        "repo",
        {
            "a3.zip": _make_plugin_zip("a", "3.0.0", deps=["b"]),
            "a2.zip": _make_plugin_zip("a", "2.0.0"),
            "a1.zip": _make_plugin_zip("a", "1.0.0"),
            "b1.zip": _make_plugin_zip("b", "1.0.0", platforms=["linux-x86_64"]),
        },
    )

    result = _invoke("--repo", str(repo_dir), "upgrade", "a")

    assert result.exit_code == 0, _get_output(result)
    assert _get_installed() == {"a": "2.0.0"}


def test_upgrade_reports_up_to_date_when_installed_version_is_excluded_by_python(
    virtual_ida_environment_with_current_python, tmp_path
):
    _install_from(tmp_path, "installed", {"a2.zip": _make_plugin_zip("a", "2.0.0")}, "a")
    repo_dir = _make_named_repo_dir(
        tmp_path,
        "repo",
        {
            "a2.zip": _make_plugin_zip("a", "2.0.0", requires_python=f">={NEXT_PYTHON}"),
            "a19.zip": _make_plugin_zip("a", "1.9.0"),
        },
    )

    result = _invoke("--repo", str(repo_dir), "upgrade", "a")

    assert result.exit_code == 0, _get_output(result)
    assert "a is already up to date (2.0.0)" in _get_output(result)
    assert _get_installed() == {"a": "2.0.0"}


def test_upgrade_without_newer_version_does_not_probe_python(virtual_ida_environment_without_python, tmp_path):
    _install_from(tmp_path, "installed", {"a2.zip": _make_plugin_zip("a", "2.0.0")}, "a")
    repo_dir = _make_named_repo_dir(
        tmp_path,
        "repo",
        {
            "a2.zip": _make_plugin_zip("a", "2.0.0", requires_python=">=3.0"),
            "a1.zip": _make_plugin_zip("a", "1.0.0", requires_python=">=3.0"),
        },
    )

    result = _invoke("--repo", str(repo_dir), "upgrade", "a")

    assert result.exit_code == 0, _get_output(result)
    assert "a is already up to date (2.0.0)" in _get_output(result)


def test_upgrade_keeps_version_when_newer_version_needs_undetectable_python(
    virtual_ida_environment_without_python, tmp_path
):
    _install_from(tmp_path, "installed", {"a2.zip": _make_plugin_zip("a", "2.0.0")}, "a")
    repo_dir = _make_named_repo_dir(
        tmp_path,
        "repo",
        {
            "a3.zip": _make_plugin_zip("a", "3.0.0", requires_python=">=3.0"),
            "a2.zip": _make_plugin_zip("a", "2.0.0"),
        },
    )

    result = _invoke("--repo", str(repo_dir), "upgrade", "a")

    assert result.exit_code == 0, _get_output(result)
    assert "a is already up to date (2.0.0)" in _get_output(result)
    assert "a 3.0.0 requires Python >=3.0, and IDA's Python cannot be detected" in _get_output(result)
    assert _get_installed() == {"a": "2.0.0"}


def test_upgrade_upgrades_dependency_below_new_pin(virtual_ida_environment, tmp_path):
    _install_from(
        tmp_path,
        "installed",
        {"a1.zip": _make_plugin_zip("a", "1.0.0", deps=["b==1.0.0"]), "b1.zip": _make_plugin_zip("b", "1.0.0")},
        "a",
    )
    repo_dir = _make_named_repo_dir(
        tmp_path,
        "repo",
        {
            "a2.zip": _make_plugin_zip("a", "2.0.0", deps=["b==2.0.0"]),
            "b1.zip": _make_plugin_zip("b", "1.0.0"),
            "b2.zip": _make_plugin_zip("b", "2.0.0"),
        },
    )

    result = _invoke("--repo", str(repo_dir), "upgrade", "a")

    assert result.exit_code == 0, _get_output(result)
    assert _get_installed() == {"a": "2.0.0", "b": "2.0.0"}


def test_upgrade_fetches_from_configured_bundle_repository(
    virtual_ida_environment_with_current_python, block_network, tmp_path
):
    _install_from(tmp_path, "installed", {"a1.zip": _make_plugin_zip("a", "1.0.0")}, "a")
    repo_dir = _make_named_repo_dir(
        tmp_path,
        "repo",
        {"a2.zip": _make_plugin_zip("a", "2.0.0", deps=["b"]), "b1.zip": _make_plugin_zip("b", "1.0.0")},
    )
    out = tmp_path / "bundle.zip"
    result = _invoke_create(repo_dir, out, [f"{find_current_ida_platform()}-{CURRENT_CP}"], ["a"])
    assert result.exit_code == 0, result.output + result.stderr
    for argv in (
        ["repo", "remove", "hexrays"],
        ["repo", "remove", "community"],
        ["repo", "add", "offline", out.as_uri()],
        ["repo", "set-default", "offline"],
    ):
        assert _invoke(*argv).exit_code == 0

    result = _invoke("upgrade", "a")

    assert result.exit_code == 0, _get_output(result)
    assert _get_installed() == {"a": "2.0.0", "b": "1.0.0"}
