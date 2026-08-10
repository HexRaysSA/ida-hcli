from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Prints the running interpreter's version as `major.minor`.
PRINT_VERSION_PY = "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"


def parse_pyvenv_cfg(path: Path) -> dict[str, str]:
    """
    Fetch key-value pairs from a pyvenv.cfg file, such as found in uv-created
    virtual environments.

    Keys are lowercased.  Returns an empty dict when the file can't be read.
    """
    result: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip().lower()] = value.strip()
    except OSError:
        pass
    return result


def _get_uv_cache_dirs() -> list[Path]:
    """
    compute the file system path to the cache directory used by uv,
    to store things like temporary virtual environments.
    """
    dirs: list[Path] = []

    uv_cache = os.environ.get("UV_CACHE_DIR")
    if uv_cache:
        dirs.append(Path(uv_cache).resolve())

    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        dirs.append(home / "Library" / "Caches" / "uv")
        dirs.append(home / ".cache" / "uv")
    elif system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            dirs.append(Path(local_app_data) / "uv" / "cache")
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        if xdg:
            dirs.append(Path(xdg) / "uv")
        dirs.append(home / ".cache" / "uv")

    return dirs


def _is_under_uv_cache(path: Path) -> bool:
    """
    is the given path found under any uv cache directory,
    such as, "is the given virtual environment found in the cache directory?"
    """
    try:
        resolved = path.resolve()
    except OSError:
        return False

    for cache_dir in _get_uv_cache_dirs():
        try:
            resolved.relative_to(cache_dir.resolve())
            return True
        except ValueError:
            continue

    return False


_UV_INTERNAL_DIRS = frozenset({"archive-v0", "builds-v0"})


def _has_uv_internal_parent(path: Path) -> bool:
    """Check if any ancestor directory has a UV-internal name.

    UV organizes its cache into directories like ``archive-v0/`` and
    ``builds-v0/``.  These names are unique to UV's internal layout —
    no user would name their project directories this way.  This catches
    ephemeral environments regardless of where the cache root is
    (``~/.cache/uv/``, ``$TMPDIR``, ``--no-cache`` temp dirs, etc.).
    """
    for parent in path.resolve().parents:
        if parent.name in _UV_INTERNAL_DIRS:
            return True
    return False


def is_uv_cache_virtual_env(virtual_env: str | Path) -> bool:
    """Detect if a virtual environment is a UV ephemeral cache/overlay environment.

    For example, if you run ``uv run --with ida-hcli hcli`` from a non-project
    directory, then uv may create a temporary virtual environment to run this
    command.  This routine detects if the given path is one of those cache
    environments.

    Three independent signals (any is sufficient):

      1. Path is under a known UV cache directory (``~/.cache/uv/``, etc.).
      2. Path has a UV-internal ancestor directory (``archive-v0/``,
         ``builds-v0/``).  Catches ``--no-cache`` and ``$TMPDIR`` layouts.
      3. pyvenv.cfg contains ``extends-environment`` — only written by
         ``uv run --with`` overlay environments (uv 0.7.9+).
    """
    path = Path(virtual_env)

    if _is_under_uv_cache(path):
        return True

    if _has_uv_internal_parent(path):
        return True

    cfg = parse_pyvenv_cfg(path / "pyvenv.cfg")
    return "extends-environment" in cfg


def get_python_exe_candidates(root: Path, version: str | None = None) -> list[Path]:
    """List the paths where a Python interpreter may live under an environment root.

    The root can be a virtualenv or an installation prefix (sys.prefix); both
    use the same layout.  `version` is a `major.minor` string that adds the
    version-suffixed name (like `bin/python3.12`) to the candidates on POSIX.
    """
    if platform.system() == "Windows":
        return [root / "Scripts" / "python.exe", root / "python.exe"]

    bindir = root / "bin"
    candidates = []
    if version:
        candidates.append(bindir / f"python{version}")
    candidates.extend([bindir / "python3", bindir / "python"])
    return candidates


def find_virtual_env_python(virtual_env: str | Path) -> Path | None:
    """Locate the Python interpreter inside a virtual environment.

    Returns None when the directory doesn't look like a virtual environment,
    or when its interpreter is missing (such as a venv whose base Python was
    uninstalled).
    """
    for candidate in get_python_exe_candidates(Path(virtual_env)):
        if candidate.is_file():
            return candidate

    return None


def probe_python_version(python_exe: Path) -> str | None:
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
            timeout=10.0,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("failed to probe version of %s: %s", python_exe, e)
        return None

    version = result.stdout.strip()
    return version or None


def read_virtual_env_version(virtual_env: str | Path) -> str | None:
    """Read the `major.minor` Python version recorded in a venv's pyvenv.cfg.

    The stdlib `venv` module writes `version`, while uv writes `version_info`;
    both are handled.  Returns None when pyvenv.cfg is missing or records no
    usable version.

    This only reports what created the venv.  Prefer running the venv's
    interpreter when it's available, since a venv can be relocated or its base
    Python replaced after pyvenv.cfg was written.
    """
    cfg = parse_pyvenv_cfg(Path(virtual_env) / "pyvenv.cfg")
    raw = cfg.get("version") or cfg.get("version_info")
    if not raw:
        return None

    parts = raw.split(".")
    if len(parts) < 2:
        return None

    try:
        return f"{int(parts[0])}.{int(parts[1])}"
    except ValueError:
        return None


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
class VenvCandidate:
    path: Path
    source: str


def find_candidate_virtual_envs() -> list[VenvCandidate]:
    """Scan PATH for virtual environments that are not the current process venv.

    When HCLI runs under `uv run --with ida-hcli`, uv replaces `$VIRTUAL_ENV`
     with its own ephemeral virtual environment.
    Based on our research, the user's real venv typically
     remains on `$PATH` (the `activate` script prepends its `bin/`).

    This function recovers those candidates.
    """
    excluded: set[Path] = set()
    excluded.add(Path(sys.prefix).resolve())

    seen: set[Path] = set()
    candidates: list[VenvCandidate] = []

    path_val = os.environ.get("PATH", "")
    for entry in path_val.split(os.pathsep):
        if not entry:
            continue

        entry_path = Path(entry)
        if entry_path.name not in ("bin", "Scripts"):
            continue

        venv_root = entry_path.parent
        cfg_path = venv_root / "pyvenv.cfg"
        if not cfg_path.is_file():
            continue

        try:
            resolved = venv_root.resolve()
        except OSError:
            continue

        if resolved in excluded:
            continue

        if resolved in seen:
            continue
        seen.add(resolved)

        candidates.append(VenvCandidate(path=venv_root, source="PATH"))

    return candidates


def resolve_user_virtual_env() -> Path | None:
    """Resolve the user's activated virtual environment.

    If `$VIRTUAL_ENV` is set and is not a uv cache overlay, returns it
    directly.  If it *is* a uv cache overlay, scans `$PATH` for the
    first non-uv-cache candidate venv.  Returns None when no user venv
    can be identified.

    When HCLI runs under `uv run --with ida-hcli`, uv replaces `$VIRTUAL_ENV`
     with its own ephemeral virtual environment.
    Based on our research, the user's real venv typically
     remains on `$PATH` (the `activate` script prepends its `bin/`).
    """
    virtual_env = os.environ.get("VIRTUAL_ENV")
    if not virtual_env:
        return None

    if not is_uv_cache_virtual_env(virtual_env):
        hcli_prefix = os.path.normcase(os.path.abspath(sys.prefix))
        if os.path.normcase(os.path.abspath(virtual_env)) == hcli_prefix:
            return None
        return Path(virtual_env)

    for candidate in find_candidate_virtual_envs():
        if not is_uv_cache_virtual_env(candidate.path):
            return candidate.path

    return None
