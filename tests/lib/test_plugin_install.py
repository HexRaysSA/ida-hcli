import contextlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fixtures import PLUGIN_DATA

from hcli.lib.ida.plugin.install import (
    can_disable_plugin,
    can_enable_plugin,
    disable_plugin,
    enable_plugin,
    get_installed_plugins,
    get_plugin_directory,
    install_plugin_archive,
    is_plugin_enabled,
    is_plugin_installed,
    uninstall_plugin,
    upgrade_plugin_archive,
)
from hcli.lib.ida.python import pip_freeze


@contextlib.contextmanager
def temp_env_var(key: str, value: str):
    _orig = os.environ.get(key, "")
    os.environ[key] = value
    try:
        yield
    finally:
        if _orig:
            os.environ[key] = _orig
        else:
            del os.environ[key]


@contextlib.contextmanager
def temp_env_var_path(key: str):
    temp_dir = tempfile.mkdtemp()
    try:
        with temp_env_var(key, temp_dir):
            yield
    finally:
        shutil.rmtree(temp_dir)


@pytest.fixture
def temp_hcli_idausr_dir():
    with temp_env_var_path("HCLI_IDAUSR"):
        yield


def test_install_source_plugin_archive(temp_hcli_idausr_dir):
    plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "plugin1")

    plugin_directory = get_plugin_directory("plugin1")
    assert plugin_directory.exists()
    assert (plugin_directory / "ida-plugin.json").exists()
    assert (plugin_directory / "plugin1.py").exists()

    assert ("plugin1", "v1.0.0") in get_installed_plugins()


def test_install_binary_plugin_archive(temp_hcli_idausr_dir):
    plugin_path = PLUGIN_DATA / "zydisinfo" / "zydisinfo-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "zydisinfo")

    plugin_directory = get_plugin_directory("zydisinfo")
    assert plugin_directory.exists()
    assert (plugin_directory / "ida-plugin.json").exists()
    assert (plugin_directory / "zydisinfo.dll").exists()
    assert (plugin_directory / "zydisinfo.so").exists()
    assert (plugin_directory / "zydisinfo.dylib").exists()

    assert ("zydisinfo", "v1.0.0") in get_installed_plugins()
    assert is_plugin_installed("zydisinfo")


def test_uninstall(temp_hcli_idausr_dir):
    plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "plugin1")
    assert ("plugin1", "v1.0.0") in get_installed_plugins()

    uninstall_plugin("plugin1")
    assert ("plugin1", "v1.0.0") not in get_installed_plugins()
    assert not is_plugin_installed("zydisinfo")


def test_disable(temp_hcli_idausr_dir):
    plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "plugin1")
    assert ("plugin1", "v1.0.0") in get_installed_plugins()
    assert is_plugin_installed("plugin1")

    assert is_plugin_enabled("plugin1")
    assert can_disable_plugin("plugin1")
    assert not can_enable_plugin("plugin1")
    disable_plugin("plugin1")
    assert is_plugin_installed("plugin1")
    assert not is_plugin_enabled("plugin1")
    assert not can_disable_plugin("plugin1")
    assert can_enable_plugin("plugin1")

    assert not is_plugin_enabled("plugin1")
    assert not can_disable_plugin("plugin1")
    assert can_enable_plugin("plugin1")
    enable_plugin("plugin1")
    assert is_plugin_installed("plugin1")
    assert is_plugin_enabled("plugin1")
    assert can_disable_plugin("plugin1")
    assert not can_enable_plugin("plugin1")

    uninstall_plugin("plugin1")
    assert ("plugin1", "v1.0.0") not in get_installed_plugins()
    assert not is_plugin_installed("zydisinfo")


def test_upgrade(temp_hcli_idausr_dir):
    v1 = (PLUGIN_DATA / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    v2 = (PLUGIN_DATA / "plugin1" / "plugin1-v2.0.0.zip").read_bytes()

    install_plugin_archive(v1, "plugin1")
    assert ("plugin1", "v1.0.0") in get_installed_plugins()
    assert is_plugin_installed("plugin1")

    upgrade_plugin_archive(v2, "plugin1")
    assert ("plugin1", "v2.0.0") in get_installed_plugins()
    assert is_plugin_installed("plugin1")

    uninstall_plugin("plugin1")

    install_plugin_archive(v2, "plugin1")
    with pytest.raises(ValueError):
        # this is a downgrade
        upgrade_plugin_archive(v1, "plugin1")


def get_python_exe_for_venv(venv_path: Path) -> Path:
    return venv_path / "Scripts" / "python.exe" if os.name == "nt" else venv_path / "bin" / "python"


def initialize_idausr_with_venv(idausr_dir: Path):
    """Initialize an IDAUSR directory with a virtual environment at IDAUSR/venv."""
    venv_path = idausr_dir / "venv"
    _ = subprocess.run(["python", "-m", "venv", str(venv_path)], check=True)
    # upgrade pip, since when we have an old python with the defaults,
    # then pip might not have --dry-run (added in 22.2)
    python_exe = get_python_exe_for_venv(venv_path)
    _ = subprocess.run([python_exe, "-m", "pip", "install", "--upgrade", "pip"])


def test_plugin_python_dependencies(temp_hcli_idausr_dir):
    idausr = Path(os.environ["HCLI_IDAUSR"])
    initialize_idausr_with_venv(idausr)

    python_exe = get_python_exe_for_venv(idausr / "venv")
    with temp_env_var("HCLI_CURRENT_IDA_PYTHON_EXE", str(python_exe.absolute())):
        plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v3.0.0.zip"
        buf = plugin_path.read_bytes()

        install_plugin_archive(buf, "plugin1")

        freeze = pip_freeze(python_exe)
        assert "packaging==25.0" in freeze
