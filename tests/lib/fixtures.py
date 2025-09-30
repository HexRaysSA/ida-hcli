import contextlib
import logging
import os
import platform
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)
_THIS_FILE = Path(__file__)
TESTS_DIR = _THIS_FILE.parent.parent
PLUGINS_DIR = TESTS_DIR / "data" / "plugins"
PROJECT_DIR = TESTS_DIR.parent


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


def install_this_package_in_venv(venv_path: Path):
    python_exe = get_python_exe_for_venv(venv_path)
    # _ = subprocess.run([python_exe, "-m", "pip", "install", str(PROJECT_DIR.absolute())], check=True)
    # using uv is a few seconds faster, which is nicer for interactive dev
    _ = subprocess.run(
        ["uv", "pip", "install", "--python=" + str(python_exe.absolute()), str(PROJECT_DIR.absolute())], check=True
    )


def run_hcli(args: str) -> subprocess.CompletedProcess[str]:
    python_exe = os.environ["HCLI_CURRENT_IDA_PYTHON_EXE"]
    if platform.system() == "Windows":
        args_list = shlex.split(args, posix=False)
    else:
        args_list = shlex.split(args)

    try:
        return subprocess.run(
            [python_exe, "-m", "hcli.main"] + args_list, check=True, encoding="utf-8", capture_output=True
        )
    except subprocess.CalledProcessError as e:
        # Log stdout and stderr on error
        logger.debug(f"hcli command failed: {' '.join([python_exe, '-m', 'hcli.main'] + args_list)}")
        logger.debug(f"hcli exit code: {e.returncode}")
        if e.stdout:
            logger.debug(f"hcli stdout: {e.stdout}")
        if e.stderr:
            logger.debug(f"hcli stderr: {e.stderr}")
        # Re-raise the original exception
        raise
