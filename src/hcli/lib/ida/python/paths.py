"""Turn IDA's reported sys/env state into a Python executable path.

Pure path logic: no subprocesses, no IDA. Shared by the idat and idapro
finders, which differ only in how they obtain the raw information.
"""

import logging
import os
import platform
from pathlib import Path

logger = logging.getLogger(__name__)


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
