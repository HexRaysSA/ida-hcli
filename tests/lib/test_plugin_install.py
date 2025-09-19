import contextlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fixtures import (
    PLUGINS_DIR,
    PROJECT_DIR,
)

from hcli.lib.ida.plugin.install import (
    get_installed_plugins,
    get_plugin_directory,
    install_plugin_archive,
    is_plugin_installed,
    uninstall_plugin,
    upgrade_plugin_archive,
)
from hcli.lib.ida.python import pip_freeze


@contextlib.contextmanager
def temp_env_var(key: str, value: str):
    """Temporarily set the given environment variable for the duration of the contextmanager block.

    Example:

        assert "HCLI_FOO" not in os.environ
        with temp_env_var("HCLI_FOO", "1"):
            assert "HCLI_FOO" in os.environ
    """
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
    """Temporarily set the given environment variable to a temporary directory for the duration of the contextmanager block.
    Cleans up the temp directory afterwards.

        Example:

            assert "HCLI_FOO" not in os.environ
            with temp_env_var_path("HCLI_FOO"):
                assert "HCLI_FOO" in os.environ
                assert Path(os.environ["HCLI_FOO"]).exists()
    """
    temp_dir = tempfile.mkdtemp()
    try:
        with temp_env_var(key, temp_dir):
            yield
    finally:
        shutil.rmtree(temp_dir)


@pytest.fixture
def temp_hcli_idausr_dir():
    """pytest fixture to set HCLI_IDAUSR to a temp directory."""
    with temp_env_var_path("HCLI_IDAUSR"):
        yield


@pytest.fixture
def hook_current_platform():
    """pytest fixture to set HCLI_CURRENT_IDA_PLATFORM to a temp directory.
    Useful so IDA doesn't need to be installed to test the the installation of plugins.
    """
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
    """pytest fixture to set HCLI_CURRENT_IDA_VERSION to a temp directory.
    Useful so IDA doesn't need to be installed to test the the installation of plugins.
    """
    with temp_env_var("HCLI_CURRENT_IDA_VERSION", "9.1"):
        yield


@pytest.fixture
def virtual_ida_environment(temp_hcli_idausr_dir, hook_current_platform, hook_current_version):
    """pytest fixture with the following hooks: IDAUSR, current platform, current version.
    This should allow many plugin operations to work without an IDA installation.
    If you need pip, use `virtual_ida_environment_with_venv`.
    """
    yield


def get_python_exe_for_venv(venv_path: Path) -> Path:
    return venv_path / "Scripts" / "python.exe" if os.name == "nt" else venv_path / "bin" / "python"


@pytest.fixture
def virtual_ida_environment_with_venv(virtual_ida_environment):
    """pytest fixture with the following hooks: IDAUSR, current platform, current version.
    There's also a Python virtualenv created at $IDAUSR/venv,
     with VIRTUAL_ENV and HCLI_CURRENT_IDA_PYTHON_EXE are set.
    """
    idausr_dir = Path(os.environ["HCLI_IDAUSR"])
    venv_path = idausr_dir / "venv"
    _ = subprocess.run(["python", "-m", "venv", str(venv_path.absolute())], check=True)

    # upgrade pip, since when we have an old python with the defaults,
    # then pip might not have --dry-run (added in 22.2)
    python_exe = get_python_exe_for_venv(venv_path)

    # using uv is a several seconds faster, which is much better for interactive dev.
    # so assume its present.
    # _ = subprocess.run([python_exe, "-m", "pip", "install", "--upgrade", "pip"])
    _ = subprocess.run(
        ["uv", "pip", "install", "--python=" + str(python_exe.absolute()), "--upgrade", "pip"], check=True
    )

    with temp_env_var("HCLI_CURRENT_IDA_PYTHON_EXE", str(python_exe.absolute())):
        with temp_env_var("VIRTUAL_ENV", str(venv_path.absolute())):
            yield


def test_install_source_plugin_archive(virtual_ida_environment):
    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "plugin1")

    plugin_directory = get_plugin_directory("plugin1")
    assert plugin_directory.exists()
    assert (plugin_directory / "ida-plugin.json").exists()
    assert (plugin_directory / "plugin1.py").exists()

    assert ("plugin1", "1.0.0") in get_installed_plugins()


def test_install_binary_plugin_archive(virtual_ida_environment):
    plugin_path = PLUGINS_DIR / "zydisinfo" / "zydisinfo-v1.0.0.zip"
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


def test_uninstall(virtual_ida_environment):
    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "plugin1")
    assert ("plugin1", "1.0.0") in get_installed_plugins()

    uninstall_plugin("plugin1")
    assert ("plugin1", "1.0.0") not in get_installed_plugins()
    assert not is_plugin_installed("zydisinfo")


def test_upgrade(virtual_ida_environment):
    v1 = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    v2 = (PLUGINS_DIR / "plugin1" / "plugin1-v2.0.0.zip").read_bytes()

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


def install_this_package_in_venv(venv_path: Path):
    python_exe = get_python_exe_for_venv(venv_path)
    # _ = subprocess.run([python_exe, "-m", "pip", "install", str(PROJECT_DIR.absolute())], check=True)
    # using uv is a few seconds faster, which is nicer for interactive dev
    _ = subprocess.run(
        ["uv", "pip", "install", "--python=" + str(python_exe.absolute()), str(PROJECT_DIR.absolute())], check=True
    )


def test_plugin_python_dependencies(virtual_ida_environment_with_venv):
    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v3.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "plugin1")

    freeze = pip_freeze(Path(os.environ["HCLI_CURRENT_IDA_PYTHON_EXE"]))
    assert "packaging==25.0" in freeze


def run_hcli(args: str) -> subprocess.CompletedProcess[str]:
    python_exe = os.environ["HCLI_CURRENT_IDA_PYTHON_EXE"]
    # Use platform-appropriate argument splitting to handle Windows paths correctly
    if platform.system() == "Windows":
        # On Windows, use shlex.split with posix=False to handle backslashes correctly
        args_list = shlex.split(args, posix=False)
    else:
        # On Unix systems, use standard shlex.split
        args_list = shlex.split(args)
    return subprocess.run(
        [python_exe, "-m", "hcli.main"] + args_list, check=True, encoding="utf-8", capture_output=True
    )


def test_plugin_all(virtual_ida_environment_with_venv):
    idausr = Path(os.environ["HCLI_IDAUSR"])
    install_this_package_in_venv(idausr / "venv")

    with temp_env_var("TERM", "dumb"):
        with temp_env_var("COLUMNS", "240"):
            p = run_hcli("--help")
            assert "Usage: python -m hcli.main [OPTIONS] COMMAND [ARGS]..." in p.stdout

            p = run_hcli("plugin --help")
            assert "Usage: python -m hcli.main plugin [OPTIONS] COMMAND [ARGS]..." in p.stdout

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

            p = run_hcli(f"plugin --repo {repo_path.absolute()} search zydisinfo")
            assert "name: zydisinfo" in p.stdout
            assert "available versions:\n 1.0.0" in p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} search zydisinfo==1.0.0")
            assert "name: zydisinfo" in p.stdout
            assert "download locations:\n IDA: 9.0-9.2  platforms: all  URL: file://" in p.stdout

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

            p = run_hcli(f"plugin --repo {repo_path.absolute()} upgrade plugin1==2.0.0")
            assert "Installed plugin: plugin1==2.0.0\n" == p.stdout

            # downgrade not supported
            with pytest.raises(subprocess.CalledProcessError) as e:
                p = run_hcli(f"plugin --repo {repo_path.absolute()} upgrade plugin1==1.0.0")
                assert (
                    e.value.stdout
                    == "Error: Cannot upgrade plugin plugin1: new version 1.0.0 is not greater than existing version 2.0.0\n"
                )

            # TODO: upgrade all

            p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
            assert " plugin1  2.0.0  upgradable to 4.0.0 \n" == p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} uninstall plugin1")
            assert "Uninstalled plugin: plugin1\n" == p.stdout

            p = run_hcli(
                f"plugin --repo {repo_path.absolute()} install {(PLUGINS_DIR / 'plugin1' / 'plugin1-v3.0.0.zip').absolute()}"
            )
            assert "Installed plugin: plugin1==3.0.0\n" == p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} uninstall plugin1")
            assert "Uninstalled plugin: plugin1\n" == p.stdout

            p = run_hcli(
                f"plugin --repo {repo_path.absolute()} install file://{(PLUGINS_DIR / 'plugin1' / 'plugin1-v4.0.0.zip').absolute()}"
            )
            assert "Installed plugin: plugin1==4.0.0\n" == p.stdout

            # TODO: install by URL
            # which will require a plugin archive with a single plugin

            # work with the default index
            # if `hint-calls` becomes unmaintained, this plugin name can be changed.
            # the point is just to show the default index works.
            p = run_hcli("plugin search hint-ca")
            assert " hint-calls  " in p.stdout

            p = run_hcli("plugin install hint-calls")
            assert "Installed plugin: hint-calls==" in p.stdout
