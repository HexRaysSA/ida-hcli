import os
import platform
from pathlib import Path

import pytest

from hcli.lib.ida import (
    find_current_ida_install_directory,
    find_current_ida_platform,
    find_current_ida_version,
    find_current_idat_executable,
    get_ida_config,
    get_ida_config_path,
)


def test_get_ida_config_path():
    result = get_ida_config_path()
    assert isinstance(result, Path)
    assert result.name == "ida-config.json"


def test_get_ida_config():
    result = get_ida_config()
    assert result is not None
    assert hasattr(result, "installation_directory")


def test_find_current_ida_install_directory():
    result = find_current_ida_install_directory()
    assert isinstance(result, Path)
    assert result.exists()
    assert result.is_dir()


def has_idat():
    if "HCLI_HAS_IDAT" not in os.environ:
        return True

    if os.environ["HCLI_HAS_IDAT"].lower() in ("", "0", "false", "f"):
        return False

    return True


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_find_current_idat_executable():
    result = find_current_idat_executable()
    assert isinstance(result, Path)
    assert result.exists()
    assert result.is_file()
    assert "idat" in result.name.lower()


# Platform-specific tests for find_current_ida_platform()
@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-specific test")
@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_find_current_ida_platform_windows():
    """Test find_current_ida_platform() on Windows."""
    result = find_current_ida_platform()
    assert isinstance(result, str)
    assert result == "windows-x86_64"


@pytest.mark.skipif(platform.system() != "Linux", reason="Linux-specific test")
@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_find_current_ida_platform_linux():
    """Test find_current_ida_platform() on Linux."""
    result = find_current_ida_platform()
    assert isinstance(result, str)
    assert result == "linux-x86_64"


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS-specific test")
@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_find_current_ida_platform_macos():
    """Test find_current_ida_platform() on macOS."""
    result = find_current_ida_platform()
    assert isinstance(result, str)
    assert result in ["macos-x86_64", "macos-aarch64"]


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_find_current_ida_version():
    """Test find_current_ida_version() returns expected version."""
    result = find_current_ida_version()
    assert isinstance(result, str)
    assert result in ["9.0", "9.1", "9.2"]
