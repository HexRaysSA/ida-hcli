import os
import contextlib
import tempfile
import shutil
from pathlib import Path

import pytest

from hcli.lib.ida import (
    get_ida_config_path,
    get_ida_config,
    find_current_ida_install_directory,
    find_current_idat_executable,
)


# may fail if dev environment is not configured with IDA
def test_get_ida_config_path():
    result = get_ida_config_path()
    assert isinstance(result, Path)
    assert result.name == "ida-config.json"


# may fail if dev environment is not configured with IDA
def test_get_ida_config():
    result = get_ida_config()
    assert result is not None
    assert hasattr(result, 'installation_directory')


# may fail if dev environment is not configured with IDA
def test_find_current_ida_install_directory():
    result = find_current_ida_install_directory()
    assert isinstance(result, Path)
    assert result.exists()
    assert result.is_dir()


# may fail if dev environment is not configured with IDA
def test_find_current_idat_executable():
    result = find_current_idat_executable()
    assert isinstance(result, Path)
    assert result.exists()
    assert result.is_file()
    assert "idat" in result.name.lower()
