"""Find how hcli was installed from PyPI and how to upgrade that installation."""

from __future__ import annotations

import enum
import logging
from pathlib import Path
from typing import Any

import httpx
from packaging.version import InvalidVersion, Version

from hcli.lib.util.io import _quote_for_shell

logger = logging.getLogger(__name__)

PACKAGE_NAME = "ida-hcli"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"


class InstallMethod(enum.Enum):
    UV_TOOL = "uv tool"
    PIPX = "pipx"
    PIP = "pip"


def get_install_method(prefix: Path) -> InstallMethod:
    """Identify the tool that manages the Python environment at `prefix`.

    uv and pipx each write a marker file into the root of the environments they create.
    """
    if (prefix / "uv-receipt.toml").is_file():
        return InstallMethod.UV_TOOL
    if (prefix / "pipx_metadata.json").is_file():
        return InstallMethod.PIPX
    return InstallMethod.PIP


def get_upgrade_command(method: InstallMethod, python_exe: str) -> list[str]:
    """Return the argv that upgrades hcli for the given install method.

    For pip, the command runs pip through `python_exe`, so it upgrades the environment
    that hcli runs from, not the one that the `pip` on PATH belongs to.
    """
    if method == InstallMethod.UV_TOOL:
        return ["uv", "tool", "upgrade", PACKAGE_NAME]
    if method == InstallMethod.PIPX:
        return ["pipx", "upgrade", PACKAGE_NAME]
    return [python_exe, "-m", "pip", "install", "--upgrade", PACKAGE_NAME]


def render_command(argv: list[str]) -> str:
    return " ".join(_quote_for_shell(arg) for arg in argv)


def get_latest_pypi_version(payload: dict[str, Any]) -> Version:
    """Return the newest stable, non-yanked release in a PyPI JSON API response.

    Raises:
        ValueError: the response lists no such release.
    """
    versions: list[Version] = []
    for raw, files in payload.get("releases", {}).items():
        if not files or all(f.get("yanked") for f in files):
            continue
        try:
            version = Version(raw)
        except InvalidVersion:
            logger.debug("ignoring invalid PyPI version: %s", raw)
            continue
        if not version.is_prerelease:
            versions.append(version)
    if not versions:
        raise ValueError(f"no releases of {PACKAGE_NAME} found on PyPI")
    return max(versions)


def fetch_latest_pypi_version() -> Version:
    """Ask PyPI for the newest stable release of hcli.

    Raises:
        httpx.HTTPError: PyPI is unreachable or returned an error.
        ValueError: PyPI lists no stable release.
    """
    response = httpx.get(PYPI_JSON_URL, timeout=10.0, follow_redirects=True)
    response.raise_for_status()
    return get_latest_pypi_version(response.json())
