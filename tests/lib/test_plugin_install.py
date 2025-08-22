import contextlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fixtures import PLUGIN_DATA

from hcli.lib.ida.plugin import (
    is_plugin_archive,
    is_binary_plugin_archive,
    is_source_plugin_archive,
)

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



@pytest.mark.asyncio
async def test_install_source_plugin_archive(temp_hcli_idausr_dir):
    plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    await install_plugin_archive(buf)

    plugin_directory = get_plugin_directory("plugin1")
    assert plugin_directory.exists()
    assert (plugin_directory / "ida-plugin.json").exists()
    assert (plugin_directory / "plugin1.py").exists()

    assert ("plugin1", "v1.0.0") in await get_installed_plugins()


@pytest.mark.asyncio
async def test_install_binary_plugin_archive(temp_hcli_idausr_dir):
    plugin_path = PLUGIN_DATA / "zydisinfo" / "zydisinfo-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    await install_plugin_archive(buf)

    plugin_directory = get_plugin_directory("zydisinfo")
    assert plugin_directory.exists()
    assert (plugin_directory / "ida-plugin.json").exists()
    assert (plugin_directory / "zydisinfo.dll").exists()
    assert (plugin_directory / "zydisinfo.so").exists()
    assert (plugin_directory / "zydisinfo.dylib").exists()

    assert ("zydisinfo", "v1.0.0") in await get_installed_plugins()
    assert await is_plugin_installed("zydisinfo")


@pytest.mark.asyncio
async def test_uninstall(temp_hcli_idausr_dir):
    plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    await install_plugin_archive(buf)
    assert ("plugin1", "v1.0.0") in await get_installed_plugins()

    await uninstall_plugin("plugin1")
    assert ("plugin1", "v1.0.0") not in await get_installed_plugins()
    assert not await is_plugin_installed("zydisinfo")


@pytest.mark.asyncio
async def test_disable(temp_hcli_idausr_dir):
    plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    await install_plugin_archive(buf)
    assert ("plugin1", "v1.0.0") in await get_installed_plugins()
    assert await is_plugin_installed("plugin1")

    assert await is_plugin_enabled("plugin1")
    assert await can_disable_plugin("plugin1")
    assert not await can_enable_plugin("plugin1")
    await disable_plugin("plugin1")
    assert await is_plugin_installed("plugin1")
    assert not await is_plugin_enabled("plugin1")
    assert not await can_disable_plugin("plugin1")
    assert await can_enable_plugin("plugin1")

    assert not await is_plugin_enabled("plugin1")
    assert not await can_disable_plugin("plugin1")
    assert await can_enable_plugin("plugin1")
    await enable_plugin("plugin1")
    assert await is_plugin_installed("plugin1")
    assert await is_plugin_enabled("plugin1")
    assert await can_disable_plugin("plugin1")
    assert not await can_enable_plugin("plugin1")

    await uninstall_plugin("plugin1")
    assert ("plugin1", "v1.0.0") not in await get_installed_plugins()
    assert not await is_plugin_installed("zydisinfo")


@pytest.mark.asyncio
async def test_upgrade(temp_hcli_idausr_dir):
    v1 = (PLUGIN_DATA / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    v2 = (PLUGIN_DATA / "plugin1" / "plugin1-v2.0.0.zip").read_bytes()

    await install_plugin_archive(v1)
    assert ("plugin1", "v1.0.0") in await get_installed_plugins()
    assert await is_plugin_installed("plugin1")

    await upgrade_plugin_archive(v2)
    assert ("plugin1", "v2.0.0") in await get_installed_plugins()
    assert await is_plugin_installed("plugin1")

    await uninstall_plugin("plugin1")

    await install_plugin_archive(v2)
    with pytest.raises(ValueError):
        # this is a downgrade
        await upgrade_plugin_archive(v1)


def initialize_idausr_with_venv(idausr_dir: Path):
    """Initialize an IDAUSR directory with a virtual environment at IDAUSR/venv."""
    venv_path = idausr_dir / "venv"
    _ = subprocess.run(["python", "-m", "venv", str(venv_path)], check=True)


@pytest.mark.asyncio
async def test_plugin_python_dependencies(temp_hcli_idausr_dir):
    idausr = Path(os.environ["HCLI_IDAUSR"])
    initialize_idausr_with_venv(idausr)

    # TODO: path probably doesn't work on Windows
    python_exe = idausr / "venv" / "bin" / "python"
    with temp_env_var("HCLI_CURRENT_IDA_PYTHON_EXE", str(python_exe.absolute())):
        plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v3.0.0.zip"
        buf = plugin_path.read_bytes()

        await install_plugin_archive(buf)

        freeze = await pip_freeze(python_exe)
        assert "packaging==25.0" in freeze

