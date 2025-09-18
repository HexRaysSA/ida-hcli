import contextlib
import shlex
import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fixtures import PLUGIN_DATA

from hcli.lib.ida.plugin.install import (
    get_installed_plugins,
    get_plugin_directory,
    install_plugin_archive,
    is_plugin_installed,
    uninstall_plugin,
    upgrade_plugin_archive,
)
from hcli.lib.ida.python import pip_freeze


def has_idat():
    """Check if idat is available (same logic as in test_ida.py)"""
    if "HCLI_HAS_IDAT" not in os.environ:
        return True

    if os.environ["HCLI_HAS_IDAT"].lower() in ("", "0", "false", "f"):
        return False

    return True


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


@pytest.fixture
def hook_current_platform():
    # hook current platform so IDA doesn't need to be installed to test the the installation of plugins

    system = platform.system()
    if system == "Windows":
        plat = "windows-x86_64"
    elif system == "Linux":
        plat = "linux-x86_64"
    elif system == "Darwin":
        # via: https://stackoverflow.com/questions/7491391/
        version = platform.uname().version
        if "RELEASE_ARM64" in version:
            plat = "macos-aarch64"
        elif "RELEASE_X86_64" in version:
            plat = "macos-x86_64"
        else:
            raise ValueError(f"Unsupported macOS version: {version}")
    else:
        raise ValueError(f"Unsupported OS: {system}")

    with temp_env_var("HCLI_CURRENT_IDA_PLATFORM", plat):
        yield


@pytest.fixture
def hook_current_version():
    # hook current platform so IDA doesn't need to be installed to test the the installation of plugins
    with temp_env_var("HCLI_CURRENT_IDA_VERSION", "9.1"):
        yield


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_install_source_plugin_archive(temp_hcli_idausr_dir, hook_current_platform, hook_current_version):
    plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "plugin1")

    plugin_directory = get_plugin_directory("plugin1")
    assert plugin_directory.exists()
    assert (plugin_directory / "ida-plugin.json").exists()
    assert (plugin_directory / "plugin1.py").exists()

    assert ("plugin1", "1.0.0") in get_installed_plugins()


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_install_binary_plugin_archive(temp_hcli_idausr_dir, hook_current_platform, hook_current_version):
    plugin_path = PLUGIN_DATA / "zydisinfo" / "zydisinfo-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "zydisinfo")

    plugin_directory = get_plugin_directory("zydisinfo")
    assert plugin_directory.exists()
    assert (plugin_directory / "ida-plugin.json").exists()
    assert (plugin_directory / "zydisinfo.dll").exists()
    assert (plugin_directory / "zydisinfo.so").exists()
    assert (plugin_directory / "zydisinfo.dylib").exists()

    assert ("zydisinfo", "1.0.0") in get_installed_plugins()
    assert is_plugin_installed("zydisinfo")


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_uninstall(temp_hcli_idausr_dir, hook_current_platform, hook_current_version):
    plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "plugin1")
    assert ("plugin1", "1.0.0") in get_installed_plugins()

    uninstall_plugin("plugin1")
    assert ("plugin1", "1.0.0") not in get_installed_plugins()
    assert not is_plugin_installed("zydisinfo")


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_upgrade(temp_hcli_idausr_dir, hook_current_platform, hook_current_version):
    v1 = (PLUGIN_DATA / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    v2 = (PLUGIN_DATA / "plugin1" / "plugin1-v2.0.0.zip").read_bytes()

    install_plugin_archive(v1, "plugin1")
    assert ("plugin1", "1.0.0") in get_installed_plugins()
    assert is_plugin_installed("plugin1")

    upgrade_plugin_archive(v2, "plugin1")
    assert ("plugin1", "2.0.0") in get_installed_plugins()
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
    # _ = subprocess.run([python_exe, "-m", "pip", "install", "--upgrade", "pip"])
    # using uv is a few seconds faster, which is nicer for interactive dev
    _ = subprocess.run(["uv", "pip", "install", "--python=" + str(python_exe.absolute()), "--upgrade", "pip"], check=True)


THIS_FILE = Path(__file__)
TESTS_DIR = THIS_FILE.parent.parent
PROJECT_DIR = TESTS_DIR.parent


def install_this_package_in_venv(venv_path: Path):
    python_exe = get_python_exe_for_venv(venv_path)
    # _ = subprocess.run([python_exe, "-m", "pip", "install", str(PROJECT_DIR.absolute())], check=True)
    # using uv is a few seconds faster, which is nicer for interactive dev
    _ = subprocess.run(["uv", "pip", "install", "--python=" + str(python_exe.absolute()), str(PROJECT_DIR.absolute())], check=True)


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_plugin_python_dependencies(temp_hcli_idausr_dir, hook_current_platform, hook_current_version):
    idausr = Path(os.environ["HCLI_IDAUSR"])
    initialize_idausr_with_venv(idausr)

    python_exe = get_python_exe_for_venv(idausr / "venv")
    with temp_env_var("HCLI_CURRENT_IDA_PYTHON_EXE", str(python_exe.absolute())):
        plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v3.0.0.zip"
        buf = plugin_path.read_bytes()

        install_plugin_archive(buf, "plugin1")

        freeze = pip_freeze(python_exe)
        assert "packaging==25.0" in freeze


def run_hcli(args: str) -> subprocess.CompletedProcess[str]:
    python_exe = os.environ["HCLI_CURRENT_IDA_PYTHON_EXE"]
    return subprocess.run([python_exe, "-m", "hcli.main"] + shlex.split(args), check=True, encoding="utf-8", capture_output=True)


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_plugin_all(temp_hcli_idausr_dir, hook_current_platform, hook_current_version):
    idausr = Path(os.environ["HCLI_IDAUSR"])
    initialize_idausr_with_venv(idausr)
    venv_path = idausr / "venv"
    install_this_package_in_venv(venv_path)

    python_exe = get_python_exe_for_venv(venv_path)
    with temp_env_var("HCLI_CURRENT_IDA_PYTHON_EXE", str(python_exe.absolute())):
        p = run_hcli("--help")
        assert "Usage: python -m hcli.main [OPTIONS] COMMAND [ARGS]..." in p.stdout

        p = run_hcli("plugin --help")
        assert "Usage: python -m hcli.main plugin [OPTIONS] COMMAND [ARGS]..." in p.stdout
       
        PLUGINS_DIR = TESTS_DIR / "data" / "plugins"
        p = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} repo snapshot")
        assert "plugin1" in p.stdout
        assert "zydisinfo" in p.stdout
        assert "1.0.0" in p.stdout
        assert "4.0.0" in p.stdout
        # ensure it looks like json
        _ = json.loads(p.stdout)

        repo_path = idausr / "repo.json"
        repo_path.write_text(p.stdout)

        p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
        assert "No plugins found\n" == p.stdout

        # current platform: macos-aarch64
        # current version: 9.1
        # 
        # plugin1    4.0.0    https://github.com/HexRaysSA/ida-hcli
        # zydisinfo  1.0.0    https://github.com/HexRaysSA/ida-hcli
        p = run_hcli(f"plugin --repo {repo_path.absolute()} search")
        assert "plugin1    4.0.0    https://github.com/HexRaysSA/ida-hcli" in p.stdout
        assert "zydisinfo  1.0.0    https://github.com/HexRaysSA/ida-hcli" in p.stdout
        
        p = run_hcli(f"plugin --repo {repo_path.absolute()} search zydis")
        assert "zydisinfo  1.0.0    https://github.com/HexRaysSA/ida-hcli" in p.stdout
        assert "plugin1    4.0.0    https://github.com/HexRaysSA/ida-hcli" not in p.stdout

        # TODO: search zydisinfo
        # TODO: search zydisinfo==1.0.0
        
        p = run_hcli(f"plugin --repo {repo_path.absolute()} install zydisinfo")
        assert "Installed plugin: zydisinfo==1.0.0\n" == p.stdout

        p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
        assert " zydisinfo  1.0.0   \n" == p.stdout

        p = run_hcli(f"plugin --repo {repo_path.absolute()} uninstall zydisinfo")
        assert "Uninstalled plugin: zydisinfo\n" == p.stdout

        p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
        assert "No plugins found\n" == p.stdout

        p = run_hcli(f"plugin --repo {repo_path.absolute()} install plugin1==1.0.0")
        assert "Installed plugin: plugin1==1.0.0\n" == p.stdout

        p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
        assert " plugin1  1.0.0  upgradable to 4.0.0 \n" == p.stdout

        # p = run_hcli(f"plugin --repo {repo_path.absolute()} upgrade plugin1==2.0.0")
        # assert "Installed plugin: plugin1==2.0.0\n" == p.stdout

        # # downgrade
        # p = run_hcli(f"plugin --repo {repo_path.absolute()} upgrade plugin1==1.0.0")
        # assert "Installed plugin: plugin1==1.0.0\n" == p.stdout

        # upgrade all
        # uninstall
        #
        # install by path
        # install by url
        # install from default index
