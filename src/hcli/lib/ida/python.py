# see also hcli.lib.util.python
import logging
import os
import platform
import subprocess
from pathlib import Path

from hcli.env import ENV
from hcli.lib.ida import run_py_in_current_idapython

logger = logging.getLogger(__name__)


# Script run inside IDA's embedded Python via idat.
# Returns sys.prefix, sys.base_prefix, and version info
# so we can detect the Python executable on the hcli side.
GET_PYTHON_INFO_PY = """
import sys
import io
import json

# ensure UTF-8 output for unicode install paths
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

print("__hcli__:" + json.dumps({
    "frozen": getattr(sys, "frozen", False),
    "prefix": sys.prefix,
    "base_prefix": sys.base_prefix,
    "version_major": sys.version_info.major,
    "version_minor": sys.version_info.minor,
}))
sys.exit()
"""


class PythonNotFoundError(RuntimeError):
    """Could not detect IDA's Python executable."""

    pass


def _derive_python_exe(info: dict) -> Path:
    """Derive the Python executable path from IDA's embedded Python sys info.

    Uses sys.prefix/sys.base_prefix to locate the Python executable.
    Checks versioned binaries first on Linux/Mac for precision.
    """
    if info.get("frozen", False):
        raise PythonNotFoundError("IDA is running as a frozen application, cannot detect Python executable")

    is_windows = platform.system() == "Windows"
    version = f"{info['version_major']}.{info['version_minor']}"

    # deduplicate while preserving order: prefix first, then base_prefix
    prefixes = list(dict.fromkeys([info["prefix"], info["base_prefix"]]))

    candidates = []
    for prefix in prefixes:
        if is_windows:
            candidates.append(os.path.join(prefix, "Scripts", "python.exe"))
            candidates.append(os.path.join(prefix, "python.exe"))
        else:
            bindir = os.path.join(prefix, "bin")
            candidates.append(os.path.join(bindir, f"python{version}"))
            candidates.append(os.path.join(bindir, "python3"))
            candidates.append(os.path.join(bindir, "python"))

    candidates = [os.path.abspath(c) for c in candidates]

    for candidate in candidates:
        logger.debug("candidate: %s (exists: %s)", candidate, os.path.exists(candidate))

    for candidate in candidates:
        if os.path.exists(candidate):
            return Path(candidate)

    raise PythonNotFoundError(
        "Could not detect IDA's Python executable.\n"
        "Please run idapyswitch to select a Python installation, then try again.\n"
        f"sys.prefix: {info['prefix']}\n"
        f"sys.base_prefix: {info['base_prefix']}\n"
        f"Tried: {candidates}"
    )


def find_current_python_executable() -> Path:
    """find the python executable associated with the current IDA installation"""
    # duplicate here, because we prefer access through ENV
    # but tests might update env vars for the current process.
    exe = os.environ.get("HCLI_CURRENT_IDA_PYTHON_EXE")
    if exe:
        return Path(exe)
    if ENV.HCLI_CURRENT_IDA_PYTHON_EXE is not None:
        return Path(ENV.HCLI_CURRENT_IDA_PYTHON_EXE)

    try:
        info = run_py_in_current_idapython(GET_PYTHON_INFO_PY)
    except RuntimeError as e:
        raise PythonNotFoundError("failed to run idat to detect IDA's Python interpreter") from e

    logger.debug("IDA Python info: %s", info)
    return _derive_python_exe(info)


def does_current_ida_have_pip(python_exe: Path, timeout=10.0) -> bool:
    """Check if pip is available in the given Python executable."""
    try:
        process = subprocess.run(
            [str(python_exe), "-c", "import pip"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout
        )
        return process.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


class CantInstallPackagesError(ValueError): ...


def _format_pip_error(stdout: bytes, stderr: bytes) -> str:
    """Format pip error output for display."""
    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()

    parts = []
    if stdout_text:
        parts.append(stdout_text)
    if stderr_text:
        parts.append(stderr_text)

    return "\n".join(parts) if parts else stdout_text


def verify_pip_can_install_packages(python_exe: Path, packages: list[str], no_build_isolation: bool = False):
    """Check if the given Python packages (e.g., "foo>=v1.0,<3") can be installed.

    This allows pip to determine if there are any version conflicts
    """
    extra_args = ["--no-build-isolation"] if no_build_isolation else []
    process = subprocess.run(
        [str(python_exe), "-m", "pip", "install", "--dry-run"] + extra_args + packages,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.stdout, process.stderr
    if process.returncode != 0:
        logger.debug("can't install packages")
        logger.debug(stdout.decode("utf-8", errors="replace"))
        logger.debug(stderr.decode("utf-8", errors="replace"))
        raise CantInstallPackagesError(_format_pip_error(stdout, stderr))


def pip_install_packages(python_exe: Path, packages: list[str], no_build_isolation: bool = False):
    """Install the given Python packages (e.g., "foo>=v1.0,<3")."""
    extra_args = ["--no-build-isolation"] if no_build_isolation else []
    process = subprocess.run(
        [str(python_exe), "-m", "pip", "install"] + extra_args + packages,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.stdout, process.stderr
    if process.returncode != 0:
        logger.debug("can't install packages")
        logger.debug(stdout.decode("utf-8", errors="replace"))
        logger.debug(stderr.decode("utf-8", errors="replace"))
        raise CantInstallPackagesError(_format_pip_error(stdout, stderr))


def pip_freeze(python_exe: Path):
    process = subprocess.run([str(python_exe), "-m", "pip", "freeze"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, _ = process.stdout, process.stderr
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, [str(python_exe), "-m", "pip", "freeze"])
    return stdout.decode("utf-8", errors="replace")
