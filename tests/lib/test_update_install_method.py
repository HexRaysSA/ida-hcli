from pathlib import Path

import pytest
from packaging.version import Version

from hcli.lib.update.install import (
    InstallMethod,
    get_install_method,
    get_latest_pypi_version,
    get_upgrade_command,
    render_command,
)


def test_uv_tool_environment_is_detected_by_its_receipt(tmp_path: Path):
    (tmp_path / "uv-receipt.toml").write_text("[tool]\n")
    assert get_install_method(tmp_path) == InstallMethod.UV_TOOL


def test_pipx_environment_is_detected_by_its_metadata(tmp_path: Path):
    (tmp_path / "pipx_metadata.json").write_text("{}")
    assert get_install_method(tmp_path) == InstallMethod.PIPX


def test_other_environments_are_pip(tmp_path: Path):
    assert get_install_method(tmp_path) == InstallMethod.PIP


def test_pip_upgrade_targets_the_running_interpreter():
    python = "/opt/My Python/bin/python3"
    assert get_upgrade_command(InstallMethod.PIP, python) == [python, "-m", "pip", "install", "--upgrade", "ida-hcli"]


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        (InstallMethod.UV_TOOL, ["uv", "tool", "upgrade", "ida-hcli"]),
        (InstallMethod.PIPX, ["pipx", "upgrade", "ida-hcli"]),
    ],
)
def test_tool_managers_upgrade_by_package_name(method: InstallMethod, expected: list[str]):
    assert get_upgrade_command(method, "/usr/bin/python3") == expected


def test_render_command_quotes_paths_with_spaces():
    rendered = render_command(["/opt/My Python/bin/python3", "-m", "pip"])
    assert rendered.endswith(" -m pip")
    assert rendered.startswith(("'", '"'))


def _release(yanked: bool = False) -> list[dict]:
    return [{"filename": "ida_hcli.whl", "yanked": yanked}]


def test_latest_pypi_version_skips_prereleases_yanked_and_empty_releases():
    payload = {
        "releases": {
            "0.27.3": _release(),
            "0.28.0": _release(yanked=True),
            "0.29.0rc1": _release(),
            "0.30.0": [],
            "0.9.0": _release(),
        }
    }
    assert get_latest_pypi_version(payload) == Version("0.27.3")


def test_latest_pypi_version_raises_without_releases():
    with pytest.raises(ValueError):
        get_latest_pypi_version({"releases": {}})
