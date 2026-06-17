# see also hcli.lib.util.python
import logging
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hcli.env import ENV
from hcli.lib.ida import run_py_in_current_idapython

logger = logging.getLogger(__name__)


# Script run inside IDA's embedded Python via idat.
# Returns enough sys/env info to detect the Python executable on the hcli side.
GET_PYTHON_INFO_PY = """
import sys
import io
import json
import os

# ensure UTF-8 output for unicode install paths
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

print("__hcli__:" + json.dumps({
    "frozen": getattr(sys, "frozen", False),
    "prefix": sys.prefix,
    "base_prefix": sys.base_prefix,
    "executable": sys.executable,
    "virtual_env": os.environ.get("VIRTUAL_ENV"),
    "idapython_venv_executable": os.environ.get("IDAPYTHON_VENV_EXECUTABLE"),
    "version_major": sys.version_info.major,
    "version_minor": sys.version_info.minor,
}))
sys.exit()
"""


class PythonNotFoundError(RuntimeError):
    """Could not detect IDA's Python executable."""


def _normalize_path(path: str | None) -> str | None:
    if not path:
        return None
    return os.path.normcase(os.path.abspath(path))


def _is_windows_store_shim(path: str | None) -> bool:
    if path is None:
        return False
    lowered = path.lower()
    return "microsoft\\windowsapps" in lowered or "microsoft/windowsapps" in lowered


def _is_python_executable_name(path: str | None) -> bool:
    if path is None:
        return False
    return "python" in os.path.basename(path).lower()


def _get_venv_root_from_python(path: str | None) -> Path | None:
    if not path or not _is_python_executable_name(path):
        return None

    exe = Path(path)
    if exe.parent.name not in ("bin", "Scripts"):
        return None

    venv_root = exe.parent.parent
    if (venv_root / "pyvenv.cfg").exists():
        return venv_root

    return None


def _get_prefix_candidates(prefix: str | None, version: str, is_windows: bool) -> list[str]:
    if not prefix:
        return []

    if is_windows:
        return [
            os.path.join(prefix, "Scripts", "python.exe"),
            os.path.join(prefix, "python.exe"),
        ]

    bindir = os.path.join(prefix, "bin")
    return [
        os.path.join(bindir, f"python{version}"),
        os.path.join(bindir, "python3"),
        os.path.join(bindir, "python"),
    ]


def _derive_python_exe(info: dict) -> Path:
    """Derive the Python executable path from IDA's embedded Python sys/env info.

    Prefers sys.prefix/sys.base_prefix, but falls back to a validated sys.executable
    when IDA launches a venv interpreter whose sys.prefix remains the base install.
    """
    if info.get("frozen", False):
        raise PythonNotFoundError("IDA is running as a frozen application, cannot detect Python executable")

    is_windows = platform.system() == "Windows"
    version = f"{info['version_major']}.{info['version_minor']}"
    sys_executable = info.get("executable")
    sys_executable_venv = _get_venv_root_from_python(sys_executable)
    requested_venv_executable = info.get("idapython_venv_executable")
    requested_venv_root = _get_venv_root_from_python(requested_venv_executable)
    virtual_env = info.get("virtual_env")
    normalized_virtual_env = _normalize_path(virtual_env)

    # deduplicate while preserving order: prefix first, then base_prefix
    prefixes = list(dict.fromkeys([info["prefix"], info["base_prefix"]]))
    prefix_candidates = [
        os.path.abspath(candidate)
        for prefix in prefixes
        for candidate in _get_prefix_candidates(prefix, version, is_windows)
    ]

    for candidate in prefix_candidates:
        logger.debug("candidate: %s (exists: %s)", candidate, os.path.exists(candidate))

    # The preferred path: sys.prefix/sys.base_prefix identify the interpreter layout.
    for candidate in prefix_candidates:
        if os.path.exists(candidate):
            candidate_venv = _get_venv_root_from_python(candidate)
            if requested_venv_root and candidate_venv == requested_venv_root:
                return Path(candidate)
            if normalized_virtual_env and _normalize_path(str(candidate_venv)) == normalized_virtual_env:
                return Path(candidate)

    if info["prefix"] != info["base_prefix"]:
        for candidate in prefix_candidates:
            if os.path.exists(candidate):
                return Path(candidate)

    # macOS can report the base framework prefix even when IDA requested a venv.
    # In that case, accept sys.executable only when it can be validated as a real venv
    # interpreter, preferably the one IDA was explicitly told to use.
    if sys_executable and os.path.exists(sys_executable) and not _is_windows_store_shim(sys_executable):
        if requested_venv_root and sys_executable_venv == requested_venv_root:
            logger.debug("using sys.executable validated by IDAPYTHON_VENV_EXECUTABLE: %s", sys_executable)
            return Path(sys_executable)

        if normalized_virtual_env and _normalize_path(str(sys_executable_venv)) == normalized_virtual_env:
            logger.debug("using sys.executable validated by VIRTUAL_ENV: %s", sys_executable)
            return Path(sys_executable)

        if requested_venv_executable and _normalize_path(sys_executable) == _normalize_path(requested_venv_executable):
            logger.debug("using sys.executable matching IDAPYTHON_VENV_EXECUTABLE: %s", sys_executable)
            return Path(sys_executable)

    # On IDA 9.4+ macOS, sys.executable may be the idat binary itself rather than a
    # Python interpreter, so the sys.executable checks above cannot validate the venv.
    # When IDAPYTHON_VENV_EXECUTABLE points to an existing, valid venv python, trust it
    # directly before falling back to the base-framework interpreter.
    if (
        requested_venv_root
        and requested_venv_executable
        and os.path.exists(requested_venv_executable)
        and _get_venv_root_from_python(requested_venv_executable) == requested_venv_root
    ):
        logger.debug("using IDAPYTHON_VENV_EXECUTABLE directly: %s", requested_venv_executable)
        return Path(requested_venv_executable)

    for candidate in prefix_candidates:
        if os.path.exists(candidate):
            return Path(candidate)

    raise PythonNotFoundError(
        "Could not detect IDA's Python executable.\n"
        "Please run idapyswitch to select a Python installation, then try again.\n"
        f"sys.prefix: {info['prefix']}\n"
        f"sys.base_prefix: {info['base_prefix']}\n"
        f"sys.executable: {info.get('executable')}\n"
        f"VIRTUAL_ENV: {info.get('virtual_env')}\n"
        f"IDAPYTHON_VENV_EXECUTABLE: {info.get('idapython_venv_executable')}\n"
        f"Tried: {prefix_candidates}"
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
        raise PythonNotFoundError(
            "failed to run idat to detect IDA's Python interpreter. "
            "If you already know the interpreter path, set HCLI_CURRENT_IDA_PYTHON_EXE=/path/to/python and retry."
        ) from e

    logger.debug("IDA Python info: %s", info)
    return _derive_python_exe(info)


def does_current_ida_have_pip(python_exe: Path, timeout=10.0) -> bool:
    """Check if pip is available in the given Python executable."""
    try:
        process = subprocess.run(
            [str(python_exe), "-c", "import pip"], capture_output=True, timeout=timeout, check=False
        )
        return process.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


class CantInstallPackagesError(ValueError): ...


@dataclass(frozen=True)
class PipOptions:
    index_url: str | None = None
    extra_index_urls: tuple[str, ...] = ()
    find_links: tuple[Path | str, ...] = ()
    offline: bool = False
    isolated: bool = False
    no_cache_dir: bool = False
    disable_pip_version_check: bool = False
    no_build_isolation: bool = False

    @property
    def has_custom_sources(self) -> bool:
        return self.index_url is not None or len(self.extra_index_urls) > 0 or len(self.find_links) > 0

    def build_args(self) -> list[str]:
        args: list[str] = []
        if self.isolated:
            args.append("--isolated")
        if self.disable_pip_version_check:
            args.append("--disable-pip-version-check")
        if self.no_cache_dir:
            args.append("--no-cache-dir")
        if self.offline:
            args.append("--no-index")
        if self.index_url:
            args.extend(["--index-url", self.index_url])
        for url in self.extra_index_urls:
            args.extend(["--extra-index-url", url])
        for link in self.find_links:
            args.extend(["--find-links", str(link)])
        if self.no_build_isolation:
            args.append("--no-build-isolation")
        return args


PIP_OPTIONS_DEFAULT = PipOptions()


def merge_bundle_pip_options(user_options: PipOptions, bundle_options: PipOptions) -> PipOptions:
    return PipOptions(
        index_url=user_options.index_url,
        extra_index_urls=user_options.extra_index_urls,
        find_links=bundle_options.find_links + user_options.find_links,
        offline=bundle_options.offline or user_options.offline,
        isolated=bundle_options.isolated or user_options.isolated,
        no_cache_dir=bundle_options.no_cache_dir or user_options.no_cache_dir,
        disable_pip_version_check=bundle_options.disable_pip_version_check or user_options.disable_pip_version_check,
        no_build_isolation=user_options.no_build_isolation,
    )


def _format_pip_error(stdout: bytes, stderr: bytes) -> str:
    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()

    parts = []
    if stdout_text:
        parts.append(stdout_text)
    if stderr_text:
        parts.append(stderr_text)

    return "\n".join(parts) if parts else stdout_text


def verify_pip_can_install_packages(
    python_exe: Path,
    packages: list[str],
    pip_options: PipOptions = PIP_OPTIONS_DEFAULT,
    no_build_isolation: bool = False,
):
    """Check if the given Python packages (e.g., "foo>=v1.0,<3") can be installed.

    Raises:
        CantInstallPackagesError: if pip dry-run fails.
    """
    effective = _merge_no_build_isolation(pip_options, no_build_isolation)
    process = subprocess.run(
        [str(python_exe), "-m", "pip", "install", "--dry-run"] + effective.build_args() + packages,
        capture_output=True,
        check=False,
    )
    stdout, stderr = process.stdout, process.stderr
    if process.returncode != 0:
        logger.debug("can't install packages")
        logger.debug(stdout.decode("utf-8", errors="replace"))
        logger.debug(stderr.decode("utf-8", errors="replace"))
        raise CantInstallPackagesError(_format_pip_error(stdout, stderr))


def pip_install_packages(
    python_exe: Path,
    packages: list[str],
    pip_options: PipOptions = PIP_OPTIONS_DEFAULT,
    no_build_isolation: bool = False,
):
    """Install the given Python packages (e.g., "foo>=v1.0,<3").

    Raises:
        CantInstallPackagesError: if pip install fails.
    """
    effective = _merge_no_build_isolation(pip_options, no_build_isolation)
    process = subprocess.run(
        [str(python_exe), "-m", "pip", "install"] + effective.build_args() + packages,
        capture_output=True,
        check=False,
    )
    stdout, stderr = process.stdout, process.stderr
    if process.returncode != 0:
        logger.debug("can't install packages")
        logger.debug(stdout.decode("utf-8", errors="replace"))
        logger.debug(stderr.decode("utf-8", errors="replace"))
        raise CantInstallPackagesError(_format_pip_error(stdout, stderr))


def _merge_no_build_isolation(pip_options: PipOptions, no_build_isolation: bool) -> PipOptions:
    if no_build_isolation and not pip_options.no_build_isolation:
        return PipOptions(
            index_url=pip_options.index_url,
            extra_index_urls=pip_options.extra_index_urls,
            find_links=pip_options.find_links,
            offline=pip_options.offline,
            isolated=pip_options.isolated,
            no_cache_dir=pip_options.no_cache_dir,
            disable_pip_version_check=pip_options.disable_pip_version_check,
            no_build_isolation=True,
        )
    return pip_options


def detect_current_python_version() -> str:
    """Detect the major.minor Python version of the active IDA Python.

    Raises if detection fails rather than silently falling back to the
    hcli interpreter's version, which may differ from IDA's Python.
    """
    logger.debug("detecting IDA Python executable...")
    python_exe = find_current_python_executable()
    logger.debug("found IDA Python executable: %s", python_exe)
    result = subprocess.run(
        [str(python_exe), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    version = result.stdout.strip()
    logger.debug("detected Python version: %s", version)
    return version


def pip_freeze(python_exe: Path):
    process = subprocess.run([str(python_exe), "-m", "pip", "freeze"], capture_output=True, check=False)
    stdout, _ = process.stdout, process.stderr
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, [str(python_exe), "-m", "pip", "freeze"])
    return stdout.decode("utf-8", errors="replace")
