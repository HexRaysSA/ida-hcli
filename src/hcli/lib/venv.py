from __future__ import annotations

import logging
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_pyvenv_cfg(path: Path) -> dict[str, str]:
    """
    fetch key-value pairs from a pyenv.cfg file, such as found in uv-created
    virtual environments.
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

    cfg = _parse_pyvenv_cfg(path / "pyvenv.cfg")
    return "extends-environment" in cfg


@dataclass(frozen=True)
class VenvCandidate:
    path: Path
    source: str
    cfg: dict[str, str] = field(default_factory=dict)


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

        cfg = _parse_pyvenv_cfg(cfg_path)
        candidates.append(VenvCandidate(path=venv_root, source="PATH", cfg=cfg))

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
