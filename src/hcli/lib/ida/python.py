from __future__ import annotations

import functools
import json
import logging
import os
import platform
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rich.markup import escape

from hcli.env import ENV
from hcli.lib.console import stderr_console
from hcli.lib.ida import run_py_in_current_idapython
from hcli.lib.venv import find_virtual_env_python, read_virtual_env_version

logger = logging.getLogger(__name__)

# Prints the running interpreter's version as `major.minor`.
PRINT_VERSION_PY = "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"


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


@functools.cache
def probe_current_python_info() -> dict:
    """Run GET_PYTHON_INFO_PY inside IDA's embedded Python, once per process.

    The probe launches IDA in batch mode via idat, which takes seconds, and its
    inputs (the IDA installation and process environment) don't change within a
    single hcli invocation, so the result is cached.  Failures are not cached:
    an exception propagates and the next call retries.

    Raises:
        RuntimeError: if idat can't be run or emits no result.
    """
    return run_py_in_current_idapython(GET_PYTHON_INFO_PY)


def resolve_current_python() -> tuple[Path, dict | None]:
    """Find IDA's Python executable, along with the probe info used to find it.

    The info is the result of running GET_PYTHON_INFO_PY inside IDA.  It's None
    when the executable came from $HCLI_CURRENT_IDA_PYTHON_EXE, because no probe
    is needed then.  Callers that want both (such as the version mismatch check)
    should use this instead of probing IDA a second time.
    """
    # duplicate here, because we prefer access through ENV
    # but tests might update env vars for the current process.
    exe = os.environ.get("HCLI_CURRENT_IDA_PYTHON_EXE")
    if exe:
        return Path(exe), None
    if ENV.HCLI_CURRENT_IDA_PYTHON_EXE is not None:
        return Path(ENV.HCLI_CURRENT_IDA_PYTHON_EXE), None

    venv_exe = os.environ.get("IDAPYTHON_VENV_EXECUTABLE") or ENV.IDAPYTHON_VENV_EXECUTABLE
    if venv_exe and Path(venv_exe).is_file():
        logger.debug("using $IDAPYTHON_VENV_EXECUTABLE: %s", venv_exe)
        return Path(venv_exe), None

    try:
        info = probe_current_python_info()
    except RuntimeError as e:
        raise PythonNotFoundError(
            "failed to run idat to detect IDA's Python interpreter. "
            "If you already know the interpreter path, set HCLI_CURRENT_IDA_PYTHON_EXE=/path/to/python and retry."
        ) from e

    logger.debug("IDA Python info: %s", info)
    return _derive_python_exe(info), info


def find_current_python_executable() -> Path:
    """find the python executable associated with the current IDA installation"""
    python_exe, _ = resolve_current_python()
    return python_exe


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

        error_text = _format_pip_error(stdout, stderr)
        if "no such option: --dry-run" in error_text:
            raise CantInstallPackagesError(
                f"pip does not support --dry-run (requires pip 22.2 or later). "
                f"Please upgrade pip: {python_exe} -m pip install --upgrade pip"
            )
        raise CantInstallPackagesError(error_text)


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
        [str(python_exe), "-c", PRINT_VERSION_PY],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    version = result.stdout.strip()
    logger.debug("detected Python version: %s", version)
    return version


def probe_python_version(python_exe: Path, timeout: float = 10.0) -> str | None:
    """Probe the major.minor version of a Python interpreter by running it.

    Returns None when the interpreter can't be run, so callers can treat this
    as best-effort.
    """
    try:
        result = subprocess.run(
            [str(python_exe), "-c", PRINT_VERSION_PY],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("failed to probe version of %s: %s", python_exe, e)
        return None

    version = result.stdout.strip()
    return version or None


def get_virtual_env_version(virtual_env: str | Path) -> str | None:
    """Detect the major.minor Python version of a virtual environment.

    Runs the venv's own interpreter, which is authoritative, and falls back to
    the version recorded in pyvenv.cfg when the interpreter is missing or can't
    be run.  Returns None when neither is available.
    """
    python_exe = find_virtual_env_python(virtual_env)
    if python_exe is not None:
        version = probe_python_version(python_exe)
        if version:
            return version

    return read_virtual_env_version(virtual_env)


@dataclass(frozen=True)
class PythonVersionMismatch:
    """A Python environment whose version disagrees with IDA's embedded Python.

    idapyswitch records which libpython IDA loads, and that library alone
    determines `sys.version_info` inside IDA.  Activating a virtualenv (via
    idapythonrc.py) doesn't change it: a venv only redirects `sys.path`.  So a
    venv built for a different major.minor version cannot supply working
    packages to IDA -- extension modules are ABI-incompatible, and even pure
    Python packages land in a `site-packages` directory that IDA's interpreter
    never looks at.  This is essentially always a misconfiguration.
    """

    # major.minor of IDA's embedded Python, as registered by idapyswitch
    ida_version: str
    # major.minor of the mismatched environment
    other_version: str
    # the mismatched environment: a virtualenv root or a Python executable
    other_path: Path
    # human-readable description of where `other_path` came from
    other_source: str


def find_python_version_mismatches(info: dict, python_exe: Path | None = None) -> list[PythonVersionMismatch]:
    """Find Python environments whose version disagrees with IDA's embedded Python.

    `info` is the result of running GET_PYTHON_INFO_PY inside IDA, which reports
    both `sys.version_info` and the virtualenv IDA activated for itself.

    `python_exe` is the interpreter hcli would use to install plugin
    dependencies, when the caller already knows it.  Otherwise it's derived
    from `info`, the same way `find_current_python_executable` does.

    Environments that can't be inspected are skipped, so this is best-effort:
    an empty result doesn't prove the environment is consistent.
    """
    ida_version = f"{info['version_major']}.{info['version_minor']}"
    mismatches: list[PythonVersionMismatch] = []

    # venv roots already reported, so a venv reached two different ways
    # (its $VIRTUAL_ENV and its interpreter) is only reported once
    seen_venvs: set[str] = set()

    candidate_venvs = [
        (info.get("virtual_env"), "the virtualenv activated inside IDA ($VIRTUAL_ENV)"),
        (
            _get_venv_root_from_python(info.get("idapython_venv_executable")),
            "the virtualenv requested by $IDAPYTHON_VENV_EXECUTABLE",
        ),
    ]

    for venv, source in candidate_venvs:
        if not venv:
            continue

        normalized = _normalize_path(str(venv))
        if normalized is None or normalized in seen_venvs:
            continue
        seen_venvs.add(normalized)

        version = get_virtual_env_version(venv)
        if version is not None and version != ida_version:
            mismatches.append(
                PythonVersionMismatch(
                    ida_version=ida_version,
                    other_version=version,
                    other_path=Path(venv),
                    other_source=source,
                )
            )

    # The interpreter hcli installs plugin dependencies with. When it's a venv
    # python, this usually restates a mismatch found above; when detection fell
    # back to a base interpreter, it can differ from IDA's Python on its own.
    if python_exe is None:
        try:
            python_exe = _derive_python_exe(info)
        except PythonNotFoundError as e:
            logger.debug("can't derive Python executable for mismatch check: %s", e)
            return mismatches

    exe_venv = _get_venv_root_from_python(str(python_exe))
    if exe_venv is None or _normalize_path(str(exe_venv)) not in seen_venvs:
        version = probe_python_version(python_exe)
        if version is not None and version != ida_version:
            mismatches.append(
                PythonVersionMismatch(
                    ida_version=ida_version,
                    other_version=version,
                    other_path=python_exe,
                    other_source="the interpreter hcli would install plugin dependencies into",
                )
            )

    return mismatches


def format_python_version_mismatch_warning(mismatches: list[PythonVersionMismatch]) -> str:
    """Render mismatches as a rich-markup warning, or "" when there are none."""
    if not mismatches:
        return ""

    ida_version = mismatches[0].ida_version
    lines = [
        "[bold yellow]Warning:[/bold yellow] IDA's Python version does not match the active virtualenv.",
        f"  IDA's embedded Python is {ida_version}, which is what idapyswitch registered.",
    ]
    for mismatch in mismatches:
        lines.append(
            f"  - {escape(mismatch.other_source)} is Python {mismatch.other_version}:"
            f" {escape(str(mismatch.other_path))}"
        )

    lines.append(
        "This is almost certainly a misconfiguration: activating a virtualenv does not change"
        " the Python version IDA runs, so packages installed there won't be importable inside IDA."
    )

    other_versions = {mismatch.other_version for mismatch in mismatches}
    if len(other_versions) == 1:
        other_version = other_versions.pop()
        lines.append(
            f"To fix it, either run idapyswitch to point IDA at Python {other_version},"
            f" or recreate the virtualenv with Python {ida_version}."
        )
    else:
        lines.append(
            "To fix it, make these versions agree:"
            " run idapyswitch to point IDA at the virtualenv's Python,"
            " or recreate the virtualenv with IDA's Python."
        )

    return "\n".join(lines)


def warn_on_python_version_mismatch(info: dict | None, python_exe: Path) -> None:
    """Warn on stderr when IDA's Python doesn't match the environment we'd install into.

    `info` is the probe info from `resolve_current_python`, or None when IDA's
    Python was not probed (so there's nothing to compare against).  Best-effort:
    a failure to inspect the environment is never fatal.
    """
    if info is None:
        return

    try:
        mismatches = find_python_version_mismatches(info, python_exe)
    except Exception as e:
        logger.debug("python version mismatch check failed: %s", e)
        return

    warning = format_python_version_mismatch_warning(mismatches)
    if warning:
        stderr_console.print(warning, highlight=False)


def pip_freeze(python_exe: Path):
    process = subprocess.run([str(python_exe), "-m", "pip", "freeze"], capture_output=True, check=False)
    stdout, _ = process.stdout, process.stderr
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, [str(python_exe), "-m", "pip", "freeze"])
    return stdout.decode("utf-8", errors="replace")


class ProbeError(RuntimeError):
    """Failed to run a probe script in IDA's Python environment."""


class ScriptNotFoundError(RuntimeError):
    """Could not find a console script in IDA's Python environment."""


# Script run by IDA's Python interpreter (not by idat).
# Locates the wrapper executable installed for a console script, like `speakeasy`.
#
# There is no API for this. Package metadata declares the entry point
# (`speakeasy = speakeasy.__main__:main`), which is a Python object reference, not a
# file: the wrapper is generated at install time and where it lands is up to the
# installer. see: https://discuss.python.org/t/can-i-retrieve-the-path-for-an-entry-point-using-importlib-metadata/79490
#
# So we resolve it in two steps:
#
#  1. the distribution's RECORD, via `Distribution.files`. This is the installer's own
#     list of the files it wrote, including generated scripts, which appear as paths
#     relative to site-packages, like `../../../bin/speakeasy`. It's authoritative:
#     the wrapper it names belongs to the same environment as the package we matched.
#     pip and uv both write RECORD, as does any wheel-spec-compliant installer.
#     (Debian strips it from apt-packaged distributions, but those interpreters are
#     externally managed, so we don't support installing into them anyway.)
#
#  2. otherwise, look for the file in the scripts directories, which is where the
#     wheel spec says installers should put it. This is only name matching, so it can
#     turn up a stale or unrelated script - hence it comes second.
#
# The wrapper's filename matches the entry point name by convention, not by rule.
# Windows installers generate a `name.exe` trampoline; older setuptools also left a
# `name-script.py` beside it.
GET_SCRIPT_INFO_PY = r"""
import io
import json
import os
import sys
import sysconfig

# ensure UTF-8 output for unicode install paths
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

name = sys.argv[1]

if os.name == "nt":
    filenames = [name + ".exe", name + ".cmd", name + ".bat", name, name + "-script.py"]
else:
    filenames = [name]

# Scan distributions for the entry point, rather than using entry_points(name=...)
# or EntryPoint.dist, which need Python 3.10+. IDA may be using an older interpreter.
entry_point = None
record_candidates = []
try:
    from importlib.metadata import distributions

    for dist in distributions():
        try:
            eps = [
                ep
                for ep in dist.entry_points
                if ep.name == name and ep.group in ("console_scripts", "gui_scripts")
            ]
        except Exception:
            continue

        if not eps:
            continue

        entry_point = {
            "name": eps[0].name,
            "value": eps[0].value,
            "group": eps[0].group,
            "distribution": dist.metadata["Name"],
            "version": dist.version,
        }

        # `files` is None when the distribution has no RECORD
        for file in dist.files or []:
            if os.path.basename(str(file)) not in filenames:
                continue
            try:
                record_candidates.append(os.path.realpath(str(dist.locate_file(file))))
            except Exception:
                pass

        break
except Exception:
    pass

scripts_dirs = []


def add_dir(path):
    if path and path not in scripts_dirs:
        scripts_dirs.append(path)


# next to the interpreter: where a virtualenv keeps its scripts.
# don't resolve symlinks here: a virtualenv's python is usually a link to the base
# interpreter, and following it would lead out of the environment we're inspecting.
add_dir(os.path.dirname(os.path.abspath(sys.executable)))
add_dir(sysconfig.get_path("scripts"))
try:
    # where `pip install --user` puts scripts
    add_dir(sysconfig.get_path("scripts", sysconfig.get_preferred_scheme("user")))
except Exception:
    add_dir(sysconfig.get_path("scripts", "nt_user" if os.name == "nt" else "posix_user"))

candidates = record_candidates + [os.path.join(d, f) for d in scripts_dirs for f in filenames]

path = None
for candidate in candidates:
    if os.path.isfile(candidate):
        path = candidate
        break

print("__hcli__:" + json.dumps({
    "name": name,
    "path": path,
    "scripts_dirs": scripts_dirs,
    "record_candidates": record_candidates,
    "entry_point": entry_point,
}))
"""


# Script run by IDA's Python interpreter (not by idat).
# Invokes a console script by its entry point: import the module, call the attribute,
# exit with its return value. That is exactly what the wheel spec defines a console
# script to do, and what the generated wrapper contains, so this is behaviorally
# identical. Used when the wrapper itself can't be found on disk.
RUN_ENTRY_POINT_PY = r"""
import sys
from importlib.metadata import distributions

name = sys.argv[1]

for dist in distributions():
    try:
        eps = [
            ep
            for ep in dist.entry_points
            if ep.name == name and ep.group in ("console_scripts", "gui_scripts")
        ]
    except Exception:
        continue

    if eps:
        sys.argv = [name] + sys.argv[2:]
        sys.exit(eps[0].load()())

sys.exit("error: no console script named " + name)
"""


@dataclass(frozen=True)
class EntryPoint:
    """A console script entry point declared by an installed distribution."""

    name: str
    value: str
    group: str
    distribution: str | None
    version: str | None

    @classmethod
    def from_probe(cls, doc: dict) -> EntryPoint:
        return cls(
            name=doc["name"],
            value=doc["value"],
            group=doc["group"],
            distribution=doc.get("distribution"),
            version=doc.get("version"),
        )


@dataclass(frozen=True)
class ScriptInfo:
    """The result of looking for a console script in a Python environment.

    `path` is the wrapper executable, when one was found on disk.
    `entry_point` is the declaration found in package metadata, which may exist
    even when no wrapper was installed (or when it was installed elsewhere).
    """

    name: str
    path: Path | None
    scripts_dirs: tuple[Path, ...]
    entry_point: EntryPoint | None

    @classmethod
    def from_probe(cls, doc: dict) -> ScriptInfo:
        entry_point = doc.get("entry_point")
        return cls(
            name=doc["name"],
            path=Path(doc["path"]) if doc.get("path") else None,
            scripts_dirs=tuple(Path(p) for p in doc.get("scripts_dirs", ())),
            entry_point=EntryPoint.from_probe(entry_point) if entry_point else None,
        )


def get_environment_for_python(python_exe: Path) -> dict[str, str]:
    """Build the environment for a process run against the given Python.

    hcli may itself run inside a virtualenv (or a uv cache overlay), so the
    inherited VIRTUAL_ENV/PYTHONHOME describe hcli's environment, not IDA's.
    """
    env = os.environ.copy()

    env.pop("PYTHONHOME", None)

    venv_root = _get_venv_root_from_python(str(python_exe))
    if venv_root:
        env["VIRTUAL_ENV"] = str(venv_root)
    else:
        env.pop("VIRTUAL_ENV", None)

    scripts_dir = str(python_exe.parent)
    path = env.get("PATH")
    env["PATH"] = f"{scripts_dir}{os.pathsep}{path}" if path else scripts_dir

    return env


def _run_probe(python_exe: Path, src: str, args: Sequence[str] = (), timeout: float = 60.0) -> dict:
    """Run a probe script with the given Python and parse its `__hcli__:` result line.

    Raises:
        ProbeError: if the interpreter can't be run, fails, or emits no result.
    """
    argv = [str(python_exe), "-c", src, *args]
    logger.debug("probing IDA Python environment: %s", argv)

    try:
        process = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            env=get_environment_for_python(python_exe),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ProbeError(f"failed to run {python_exe}: {e}") from e

    if process.returncode != 0:
        raise ProbeError(f"{python_exe} exited with status {process.returncode}: {process.stderr.strip()}")

    for line in process.stdout.splitlines():
        if line.startswith("__hcli__:"):
            return json.loads(line[len("__hcli__:") :])

    raise ProbeError(f"no result from {python_exe}: {process.stdout.strip()}")


def get_script_info(python_exe: Path, name: str) -> ScriptInfo:
    """Look for the console script `name` in the given Python environment.

    Raises:
        ProbeError: if the environment can't be inspected.
    """
    doc = _run_probe(python_exe, GET_SCRIPT_INFO_PY, [name])
    logger.debug("script probe: %s", doc)
    return ScriptInfo.from_probe(doc)


def find_script(python_exe: Path, name: str) -> Path:
    """Resolve the path of the console script `name` in the given Python environment.

    Raises:
        ProbeError: if the environment can't be inspected.
        ScriptNotFoundError: if no such script is installed.
    """
    info = get_script_info(python_exe, name)
    if info.path is None:
        raise ScriptNotFoundError(render_script_not_found(info))
    return info.path


def render_script_not_found(info: ScriptInfo) -> str:
    """Explain why a console script couldn't be resolved to a path."""
    searched = "\n".join(f"  {d}" for d in info.scripts_dirs)

    if info.entry_point:
        distribution = " ".join(filter(None, [info.entry_point.distribution or "?", info.entry_point.version]))
        return (
            f"script '{info.name}' is declared by {distribution} ({info.entry_point.value}), "
            f"but no program for it was installed.\n"
            f"Searched that distribution's file list and:\n{searched}\n"
            f"Try reinstalling {info.entry_point.distribution or distribution}."
        )

    return f"script '{info.name}' is not installed. Searched:\n{searched}"


def run_in_python_environment(python_exe: Path, argv: Sequence[str]) -> int:
    """Run a command in the given Python environment, connected to this terminal.

    Returns the exit status of the command.

    Raises:
        OSError: if the command can't be executed.
    """
    logger.debug("running: %s", list(argv))

    try:
        process = subprocess.run(list(argv), check=False, env=get_environment_for_python(python_exe))
    except KeyboardInterrupt:
        # the child received SIGINT too, and has already exited.
        return 130

    return process.returncode


def run_script(python_exe: Path, name: str, args: Sequence[str] = ()) -> int:
    """Run the console script `name` in the given Python environment.

    Prefers the installed wrapper executable, so the script sees exactly what it
    would when invoked from the shell. Falls back to invoking the entry point
    directly when the wrapper isn't on disk, for example when an installer put it
    somewhere we don't know to look.

    Returns the exit status of the script.

    Raises:
        ProbeError: if the environment can't be inspected.
        ScriptNotFoundError: if no such script is installed.
    """
    info = get_script_info(python_exe, name)

    if info.path is not None:
        if os.access(info.path, os.X_OK):
            return run_in_python_environment(python_exe, [str(info.path), *args])
        # a wrapper without the executable bit is still a Python script we can run.
        return run_in_python_environment(python_exe, [str(python_exe), str(info.path), *args])

    if info.entry_point is not None:
        logger.debug("no wrapper for '%s', invoking entry point %s", name, info.entry_point.value)
        return run_in_python_environment(python_exe, [str(python_exe), "-c", RUN_ENTRY_POINT_PY, name, *args])

    raise ScriptNotFoundError(render_script_not_found(info))
